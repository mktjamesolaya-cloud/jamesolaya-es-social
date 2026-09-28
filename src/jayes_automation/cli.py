"""Command line entry point.

Each command is a ``cmd_*`` function returning an exit code, dispatched from a
table. The previous version was a 47-line if/elif chain with the pilot's video
id hardcoded in four places, which made it both untestable and impossible to
run for any other video.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
from collections.abc import Callable
from dataclasses import asdict
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

from . import adaptar_es, hosting, ingest, insights, planejar_es, planner, scheduling
from . import captions as captions_mod
from . import captions_es as legendas_es
from . import publisher as publisher_mod
from . import queue as queue_mod
from .paths import Paths
from .ranking import rank_videos

#: A partir de quantos dias de token restante o doctor reprova. 21 da tres
#: semanas de folga: o cron de renovacao roda dia 1 de cada mes, entao mesmo
#: uma execucao perdida ainda cabe dentro da janela.
TOKEN_ALERTA_DIAS = 21

#: Quanto tempo sem renovar ja indica que o cron mensal parou.
TOKEN_RENOVACAO_MAX_DIAS = 45


def _emit(payload: Any) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


def _paths(args: argparse.Namespace) -> Paths:
    return Paths.resolve(getattr(args, "root", None))


# ---------------------------------------------------------------------------
# Collection
# ---------------------------------------------------------------------------


def cmd_audit_tiktok(args: argparse.Namespace) -> int:
    from .tiktok import inventory, save_inventory

    paths = _paths(args)
    output = args.output or paths.inventory
    data = json.loads(args.input.read_text(encoding="utf-8")) if args.input else inventory()
    save_inventory(data, output)

    ranked = rank_videos(data.get("entries") or [])
    if not ranked:
        print("Nenhum video ranqueavel no inventario", file=sys.stderr)
        return 1
    csv_path = output.with_name("tiktok_ranking.csv")
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(asdict(ranked[0]).keys()))
        writer.writeheader()
        writer.writerows(asdict(item) for item in ranked)
    _emit({"entries": len(data.get("entries") or []), "ranked": len(ranked), "csv": str(csv_path)})
    return 0


def cmd_download_archive(args: argparse.Namespace) -> int:
    from .tiktok import download_archive

    paths = _paths(args)
    data = json.loads((args.inventory or paths.inventory).read_text(encoding="utf-8"))
    entries = data.get("entries") or []
    ranked = rank_videos(entries)
    by_id = {str(entry.get("id")): entry for entry in entries}
    ordered = [by_id[item.id] for item in ranked if item.id in by_id]

    downloaded, failed = download_archive(
        ordered,
        args.output or paths.tiktok_dir,
        args.archive or paths.downloaded,
        args.errors or paths.download_errors,
        args.limit,
        sleep_seconds=args.sleep,
    )
    _emit({"downloaded": downloaded, "failed": failed})
    return 0


# ---------------------------------------------------------------------------
# Captions
# ---------------------------------------------------------------------------


def _ranked_ids(paths: Paths) -> list[planner.Candidate]:
    return planner.read_ranking(paths.ranking_csv)


def cmd_draft_captions(args: argparse.Namespace) -> int:
    paths = _paths(args)
    candidates = _ranked_ids(paths)
    if args.ids:
        wanted = set(args.ids)
        candidates = [item for item in candidates if item.tiktok_id in wanted]
    elif args.top:
        candidates = candidates[: args.top]

    generated, cached, skipped = 0, 0, []
    for candidate in candidates:
        info = paths.tiktok_info(candidate.tiktok_id)
        if not info.exists():
            skipped.append({"id": candidate.tiktok_id, "reason": "sem .info.json"})
            continue
        metadata = captions_mod.build_metadata(info, rank=candidate.rank, score=candidate.score)
        record, fresh = captions_mod.draft(
            candidate.tiktok_id, metadata, paths.caption(candidate.tiktok_id), force=args.force
        )
        captions_mod.save(record, paths.caption(candidate.tiktok_id))
        generated += int(fresh)
        cached += int(not fresh)
        if fresh:
            print(f"CAPTION_OK {candidate.tiktok_id}", flush=True)

    _emit({"gerados": generated, "reaproveitados": cached, "pulados": skipped})
    return 0


def cmd_import_captions(args: argparse.Namespace) -> int:
    """Load hand-written captions from a JSON file.

    The escape hatch for working without an ANTHROPIC_API_KEY: the captions are
    written elsewhere and imported here, still passing through the same
    validator and the same approval gate.
    """
    paths = _paths(args)
    entries = json.loads(args.file.read_text(encoding="utf-8"))
    ranking = {item.tiktok_id: item for item in _ranked_ids(paths)}

    imported, blocked = [], []
    for entry in entries:
        video_id = str(entry["tiktok_id"])
        info = paths.tiktok_info(video_id)
        if not info.exists():
            blocked.append({"id": video_id, "reason": "sem .info.json"})
            continue
        candidate = ranking.get(video_id)
        metadata = captions_mod.build_metadata(
            info,
            rank=candidate.rank if candidate else None,
            score=candidate.score if candidate else None,
        )
        record = captions_mod.from_text(
            video_id,
            metadata,
            caption=entry["caption"],
            hashtags=entry.get("hashtags") or [],
            alt_text=entry.get("alt_text") or "",
            author=args.author,
        )
        if args.approve:
            try:
                captions_mod.approve(record)
            except captions_mod.CaptionError as error:
                captions_mod.save(record, paths.caption(video_id))
                blocked.append({"id": video_id, "reason": str(error)})
                continue
        captions_mod.save(record, paths.caption(video_id))
        imported.append({"id": video_id, "status": record["status"]})

    _emit({"importadas": imported, "bloqueadas": blocked})
    return 1 if blocked and not imported else 0


def cmd_review_captions(args: argparse.Namespace) -> int:
    paths = _paths(args)
    records = sorted(paths.captions_dir.glob("*.json")) if paths.captions_dir.exists() else []
    shown = 0
    for path in records:
        record = captions_mod.load(path) or {}
        if args.status and record.get("status") != args.status:
            continue
        shown += 1
        text = record.get("caption") or ""
        tags = " ".join(record.get("hashtags") or [])
        print(f"\n=== {record.get('tiktok_id')} [{record.get('status')}] ===")
        print(f"{text}\n{tags}")
        print(f"({len(text)} caracteres + {len(record.get('hashtags') or [])} hashtags)")
        for warning in record.get("warnings") or []:
            print(f"  AVISO: {warning}")
    if not shown:
        print("Nenhuma legenda encontrada. Rode 'jayes draft-captions' primeiro.")
    else:
        print(f"\n{shown} legenda(s). Edite os arquivos em {paths.captions_dir} se quiser ajustar.")
    return 0


def cmd_approve_caption(args: argparse.Namespace) -> int:
    paths = _paths(args)
    targets = (
        [paths.caption(video_id) for video_id in args.ids]
        if args.ids
        else sorted(paths.captions_dir.glob("*.json"))
    )
    approved, blocked = [], []
    for path in targets:
        record = captions_mod.load(path)
        if record is None:
            blocked.append({"id": path.stem, "reason": "arquivo nao existe"})
            continue
        if record.get("status") == "approved" and not args.force:
            continue
        try:
            # validador espanhol, nao o herdado: o do projeto irmao exige uma
            # frase de ate 125 caracteres, e as legendas do @jamesolaya tem
            # mediana de 816 -- aprovar por aquelas regras reprovaria tudo
            legendas_es.aprovar(record, forcar=args.force)
            captions_mod.save(record, path)
            approved.append(record["tiktok_id"])
        except legendas_es.LegendaError as error:
            captions_mod.save(record, path)
            blocked.append({"id": record.get("tiktok_id"), "reason": str(error)})

    _emit({"aprovadas": approved, "bloqueadas": blocked})
    return 1 if blocked and not approved else 0


# ---------------------------------------------------------------------------
# Media preparation
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Acervo em espanhol
# ---------------------------------------------------------------------------
#
# A midia e a triagem vivem no projeto de analise (FormatosValidadosJamesolaya),
# nao aqui: este repo guarda a decisao (veto, legenda, receita de edicao) e a
# fila. Sao raizes diferentes de proposito, e ja houve um bug de ler as duas do
# mesmo lugar -- por isso os caminhos sao explicitos.

ACERVO_PADRAO = Path.home() / "PROJETOS_DEV" / "FormatosValidadosJamesolaya"


def _acervo(args: argparse.Namespace) -> tuple[Path, Path]:
    """(pasta de midia, pasta de dados da analise)."""
    raiz = Path(
        getattr(args, "acervo", None) or os.environ.get("JAYES_ACERVO") or ACERVO_PADRAO
    ).expanduser()
    return raiz / "melhores-conteudos", raiz / "data"


def _candidatos(args: argparse.Namespace, paths: Paths) -> list[ingest.Candidato]:
    midia, dados = _acervo(args)
    if not midia.is_dir():
        raise SystemExit(
            f"Acervo nao encontrado em {midia}. Passe --acervo ou defina JAYES_ACERVO."
        )
    return ingest.carregar(midia, dados, dir_vetos=paths.data, dir_adaptado=paths.midia_adaptada)


def cmd_adaptar_es(args: argparse.Namespace) -> int:
    """Renderiza a midia reeditada descrita em data/adaptacoes-es.json."""
    paths = _paths(args)
    midia, _ = _acervo(args)
    receitas = adaptar_es.carregar(paths.data / adaptar_es.ARQUIVO)
    if not receitas:
        _emit({"adaptadas": [], "nota": "nenhuma receita em data/adaptacoes-es.json"})
        return 0

    # o short_code nao diz em que pasta o post esta; a triagem diz
    pastas = {c.short_code: c for c in _candidatos(args, paths)}
    feitas, falhas = [], []
    for short_code, receita in receitas.items():
        candidato = pastas.get(short_code)
        if candidato is None:
            falhas.append({"id": short_code, "motivo": "nao esta entre os candidatos"})
            continue
        origem = midia / candidato.pasta / (receita.get("arquivo") or "video.mp4")
        try:
            destino = adaptar_es.aplicar(
                short_code, origem, receita, paths.midia_adaptada, refazer=args.refazer
            )
        except adaptar_es.AdaptacaoError as erro:
            falhas.append({"id": short_code, "motivo": str(erro)})
            continue
        feitas.append({"id": short_code, "arquivo": str(destino), "bytes": destino.stat().st_size})
    _emit({"adaptadas": feitas, "falhas": falhas})
    return 1 if falhas and not feitas else 0


def cmd_importar_legendas_es(args: argparse.Namespace) -> int:
    """Carrega legendas em espanhol escritas a mao e as passa pelo validador.

    Substitui o 'import-captions' herdado, que exigia um '.info.json' do TikTok
    -- arquivo que neste projeto nunca existiu.
    """
    paths = _paths(args)
    entradas = json.loads(args.file.read_text(encoding="utf-8"))
    candidatos = {c.short_code: c for c in _candidatos(args, paths)}

    gravadas, bloqueadas = [], []
    for entrada in entradas:
        short_code = str(entrada["tiktok_id"])
        candidato = candidatos.get(short_code)
        if candidato is None:
            bloqueadas.append({"id": short_code, "motivo": "nao esta entre os candidatos"})
            continue
        # o fingerprint amarra a legenda ao post de origem: se a legenda
        # original ou o formato mudarem, da para saber que ela envelheceu
        metadados = {
            "short_code": short_code,
            "formato": candidato.formato,
            "balde": candidato.balde,
            "legenda_original": candidato.legenda_original,
        }
        registro = legendas_es.montar(
            short_code,
            metadados,
            caption=entrada["caption"],
            hashtags=entrada.get("hashtags") or [],
            alt_text=entrada.get("alt_text") or "",
            autor=args.autor,
        )
        if args.aprovar:
            try:
                legendas_es.aprovar(registro)
            except legendas_es.LegendaError as erro:
                captions_mod.save(registro, paths.caption(short_code))
                bloqueadas.append({"id": short_code, "motivo": str(erro)})
                continue
        captions_mod.save(registro, paths.caption(short_code))
        gravadas.append({"id": short_code, "status": registro["status"]})

    _emit({"gravadas": gravadas, "bloqueadas": bloqueadas})
    return 1 if bloqueadas and not gravadas else 0


def cmd_plan_es(args: argparse.Namespace) -> int:
    """Monta a fila a partir do acervo, das legendas aprovadas e dos slots.

    Este comando existe porque 'planejar_es.montar_fila' tinha onze testes e
    nenhum chamador: a primeira fila foi montada por um script avulso digitado
    de memoria. E o mesmo descasamento entre o que roda e o que esta testado
    que deixou o cron morto por quatro execucoes.
    """
    paths = _paths(args)
    fila = queue_mod.load_queue(paths.queue)
    config = scheduling.load_slots(paths.slots)
    tz = ZoneInfo(config.get("timezone", scheduling.TIMEZONE))

    ja_na_fila = {item["tiktok_id"] for item in fila["items"]}
    candidatos = [c for c in _candidatos(args, paths) if c.short_code not in ja_na_fila]
    aprovadas = legendas_es.carregar_aprovadas(paths.captions_dir)

    # nao agendar por cima do que ja esta marcado
    ocupados = [
        datetime.fromisoformat(item["scheduled_at"])
        for item in fila["items"]
        if item.get("status") in {"scheduled", "retry"} and item.get("scheduled_at")
    ]
    agora = datetime.now(tz)
    nao_antes = max([*ocupados, agora]) if ocupados else agora

    def upload_falso(caminho: Path, tag: str, **kw: Any) -> dict[str, Any]:
        return {
            "release_tag": tag,
            "asset_name": caminho.name,
            "asset_url": f"dry-run://{tag}/{caminho.name}",
            "sha256": "dry-run",
            "bytes": caminho.stat().st_size,
        }

    itens, resumo = planejar_es.montar_fila(
        candidatos,
        config,
        quantidade=args.quantidade,
        alvo_video=args.alvo_video,
        legendas=aprovadas,
        nao_antes=nao_antes,
        ocupados=ocupados,
        upload=upload_falso if args.dry_run else None,
    )

    resumo["legendas_aprovadas"] = len(aprovadas)
    resumo["agenda"] = [
        {
            "id": i["tiktok_id"],
            "tipo": i["media"]["kind"],
            "publico": i["scheduled_at"],
            "operador": i["scheduled_at_operador"],
        }
        for i in itens
    ]
    if args.dry_run:
        resumo["dry_run"] = True
        _emit(resumo)
        return 0

    fila["items"].extend(itens)
    queue_mod.save_queue(fila, paths.queue)
    resumo["gravados"] = len(itens)
    _emit(resumo)
    return 0


def cmd_rotina_status(args: argparse.Namespace) -> int:
    """Tudo que a rotina horaria precisa para decidir se trabalha, num comando.

    Existe por economia: a rotina dispara de hora em hora e, na maioria das
    vezes, a resposta certa e nao fazer nada. Sem isto cada disparo abriria
    quatro arquivos e rodaria o ingest inteiro so para descobrir que a fila
    ainda esta cheia.

    'trabalhar' e a unica chave que importa. As outras existem para explicar o
    porque -- e para o cliente conseguir auditar a decisao depois.
    """
    paths = _paths(args)
    fila = queue_mod.load_queue(paths.queue)
    config = scheduling.load_slots(paths.slots)
    tz = ZoneInfo(config.get("timezone", scheduling.TIMEZONE))
    agora = datetime.now(tz)

    agendados = [i for i in fila["items"] if i.get("status") in {"scheduled", "retry"}]
    presos = [i["id"] for i in fila["items"] if i.get("status") == "publishing"]
    horarios = [datetime.fromisoformat(i["scheduled_at"]) for i in agendados]
    ultimo = max(horarios) if horarios else agora
    folga_dias = round((ultimo - agora).total_seconds() / 86400, 2)

    git = subprocess.run(
        ["git", "status", "--porcelain"],
        capture_output=True,
        text=True,
        cwd=paths.root,
    )
    # Fora de um repositorio o git falha. Nao bloqueia: a rotina roda 'git pull'
    # antes disso e teria morrido ali. Mas sai na resposta, para "nao sei" nunca
    # se confundir com "esta limpo".
    sujo = git.stdout.strip() if git.returncode == 0 else ""
    git_disponivel = git.returncode == 0

    motivos: list[str] = []
    if presos:
        motivos.append(f"item preso em publishing: {', '.join(presos)}")
    if sujo:
        motivos.append(f"{len(sujo.splitlines())} arquivo(s) sem commit")
    if folga_dias >= args.folga_dias:
        motivos.append(f"fila cobre {folga_dias} dias (alvo: {args.folga_dias})")

    resposta: dict[str, Any] = {
        "trabalhar": not motivos,
        "motivos_para_nao": motivos,
        "itens_agendados": len(agendados),
        "folga_dias": folga_dias,
        "ultimo_agendado": ultimo.isoformat() if horarios else None,
        "git_legivel": git_disponivel,
    }

    # O ingest so roda se a decisao ja for de trabalhar: e a parte cara desta
    # checagem, e nao faz sentido paga-la para confirmar uma fila cheia.
    if not motivos:
        na_fila = {i["tiktok_id"] for i in fila["items"]}
        aprovadas = legendas_es.carregar_aprovadas(paths.captions_dir)
        pulados = (
            json.loads((paths.data / "pulados-es.json").read_text(encoding="utf-8")).get(
                "pulados", {}
            )
            if (paths.data / "pulados-es.json").exists()
            else {}
        )
        livres = [
            c
            for c in _candidatos(args, paths)
            if c.short_code not in na_fila
            and c.short_code not in aprovadas
            and c.short_code not in pulados
            and c.balde == "A"
        ]
        resposta["candidatos_livres"] = len(livres)
        resposta["proximos"] = [
            {
                "id": c.short_code,
                "tipo": c.media_kind,
                "score": round(c.score, 2),
                "pasta": str(_acervo(args)[0] / c.pasta),
            }
            for c in livres[: args.quantos]
        ]
        if len(livres) < 10:
            resposta["trabalhar"] = False
            resposta["motivos_para_nao"].append(
                f"so restam {len(livres)} candidatos livres; avise o cliente"
            )

    _emit(resposta)
    return 0


def cmd_prepare(args: argparse.Namespace) -> int:
    paths = _paths(args)
    if args.id:
        video_ids = list(args.id)
    elif args.all_approved:
        video_ids = [
            path.stem
            for path in sorted(paths.captions_dir.glob("*.json"))
            if (captions_mod.load(path) or {}).get("status") == "approved"
        ]
    else:
        print("Informe --id <tiktok_id> ou --all-approved", file=sys.stderr)
        return 2

    # Import tardio de proposito: media puxa av e imageio-ffmpeg, que so existem
    # no extra "local". O runner instala so o core e nunca prepara video -- mas
    # se este import ficasse no topo do modulo, ele quebraria ate o 'doctor'.
    from .media import normalize_for_instagram, write_report

    prepared, skipped, failed = [], [], []
    for video_id in video_ids:
        source = paths.tiktok_source(video_id)
        target = paths.ready(video_id)
        if not source.exists():
            failed.append({"id": video_id, "reason": "fonte ausente em media/tiktok"})
            continue
        if target.exists() and not args.force:
            skipped.append(video_id)
            continue
        try:
            report = normalize_for_instagram(source, target)
            write_report(target, paths.ready_report(video_id))
            prepared.append({"id": video_id, "duration": report["media"]["duration_seconds"]})
            print(f"PREPARE_OK {video_id}", flush=True)
        except RuntimeError as error:
            failed.append({"id": video_id, "reason": str(error)})
            print(f"PREPARE_ERROR {video_id}: {error}", flush=True)

    _emit({"preparados": prepared, "ja_prontos": skipped, "falhas": failed})
    return 1 if failed else 0


# ---------------------------------------------------------------------------
# Planning and hosting
# ---------------------------------------------------------------------------


def cmd_plan_queue(args: argparse.Namespace) -> int:
    """Planejador do projeto irmao. Nao serve aqui -- e uma armadilha.

    'planner.plan_queue' casa video do TikTok com horario e assume um arquivo
    por post. Aqui a origem e uma pasta ja triada e um post pode ter dez
    arquivos em ordem. Rodar isto por engano montaria uma fila errada em
    silencio, que e o modo de falha caro deste projeto.
    """
    print(
        "plan-queue e o planejador do TikTok e nao vale para o @jamesolaya.es.\n"
        "Use 'jayes plan-es --quantidade N'.",
        file=sys.stderr,
    )
    return 2


def cmd_refresh_captions(args: argparse.Namespace) -> int:
    paths = _paths(args)
    queue = queue_mod.load_queue(paths.queue)
    if any(item.get("status") == "publishing" for item in queue["items"]):
        print("Ha item em 'publishing'; rode 'jayes reconcile' antes.", file=sys.stderr)
        return 1

    resultado = planner.refresh_captions(queue, paths, ids=args.ids)
    if resultado["trocadas"] and not args.dry_run:
        queue_mod.save_queue(queue, paths.queue)
    resultado["dry_run"] = args.dry_run
    _emit(resultado)
    return 0


def cmd_reschedule(args: argparse.Namespace) -> int:
    paths = _paths(args)
    queue = queue_mod.load_queue(paths.queue)
    if any(item.get("status") == "publishing" for item in queue["items"]):
        print("Ha item em 'publishing'; rode 'jayes reconcile' antes.", file=sys.stderr)
        return 1

    config = scheduling.load_slots(paths.slots)
    try:
        por_dia = args.per_day or int(config.get("posts_per_day", 2))
        resultado = planner.reschedule(queue, config, per_day=por_dia)
    except scheduling.SchedulingError as error:
        print(f"ERRO: {error}", file=sys.stderr)
        return 1

    if resultado.get("redatados") and not args.dry_run:
        queue_mod.save_queue(queue, paths.queue)
    resultado["dry_run"] = args.dry_run
    _emit(resultado)
    return 0


def _cover_targets(queue: dict[str, Any], ids: list[str] | None) -> list[dict[str, Any]]:
    """Itens que ainda podem receber capa: publicado nao volta atras."""
    wanted = set(ids or [])
    return [
        item
        for item in queue["items"]
        if item.get("status") in {"planned", "prepared", "hosted", "scheduled"}
        and (not wanted or item["tiktok_id"] in wanted)
    ]


def cmd_remesclar(args: argparse.Namespace) -> int:
    """Alterna video e estatico na fila ja agendada, sem mexer nos horarios."""
    paths = _paths(args)
    fila = queue_mod.load_queue(paths.queue)
    if any(i.get("status") == "publishing" for i in fila["items"]):
        print("Ha item em 'publishing'; rode 'jayes reconcile' antes.", file=sys.stderr)
        return 1
    resultado = planejar_es.remesclar(fila, alvo_video=args.alvo_video)
    if not args.dry_run and resultado.get("remesclados"):
        queue_mod.save_queue(fila, paths.queue)
    resultado["dry_run"] = args.dry_run
    resultado["agenda"] = [
        {
            "operador": i.get("scheduled_at_operador"),
            "tipo": (i.get("media") or {}).get("kind"),
            "id": i["tiktok_id"],
        }
        for i in fila["items"]
        if i.get("status") in {"scheduled", "retry"}
    ]
    _emit(resultado)
    return 0


def cmd_pick_covers(args: argparse.Namespace) -> int:
    from . import covers

    paths = _paths(args)
    queue = queue_mod.load_queue(paths.queue)
    targets = _cover_targets(queue, args.ids)
    if not targets:
        _emit({"capas": [], "nota": "nenhum item elegivel"})
        return 0

    chosen, failed = [], []
    for item in targets:
        # setdefault nao serve aqui: um item recem-planejado tem a chave "media"
        # presente com valor None, entao setdefault devolve o None em vez de
        # criar o dicionario.
        media = item.get("media") or {}
        item["media"] = media
        if media.get("thumb_offset_ms") is not None and not args.force:
            continue
        source = paths.ready(item["tiktok_id"])
        try:
            options = covers.candidates(source)
            best = max(options, key=lambda c: c.score)
            media["thumb_offset_ms"] = best.offset_ms
            media["thumb_offset_source"] = "auto"
            covers.export_frame(source, best.offset_ms, paths.cover_preview(item["tiktok_id"]))
            covers.contact_sheet(
                source,
                [c.offset_ms for c in options],
                paths.cover_sheet(item["tiktok_id"]),
            )
            chosen.append(
                {
                    "id": item["tiktok_id"],
                    "em": round(best.offset_ms / 1000, 2),
                    "preview": str(paths.cover_preview(item["tiktok_id"]).relative_to(paths.root)),
                }
            )
            print(f"COVER_OK {item['tiktok_id']} @ {best.offset_ms / 1000:.2f}s", flush=True)
        except covers.CoverError as error:
            failed.append({"id": item["tiktok_id"], "reason": str(error)})
            print(f"COVER_ERROR {item['tiktok_id']}: {error}", flush=True)

    queue_mod.save_queue(queue, paths.queue)
    _emit({"capas": chosen, "falhas": failed})
    return 1 if failed else 0


def cmd_set_cover(args: argparse.Namespace) -> int:
    """Fixar a capa num instante escolhido a mao."""
    from . import covers

    paths = _paths(args)
    queue = queue_mod.load_queue(paths.queue)
    targets = _cover_targets(queue, [args.id])
    if not targets:
        print(f"Nenhum item editavel com tiktok_id {args.id}", file=sys.stderr)
        return 1

    offset_ms = int(round(args.at * 1000))
    item = targets[0]
    media = item.setdefault("media", {})
    media["thumb_offset_ms"] = offset_ms
    media["thumb_offset_source"] = "manual"
    try:
        preview = covers.export_frame(paths.ready(args.id), offset_ms, paths.cover_preview(args.id))
    except covers.CoverError as error:
        print(f"ERRO: {error}", file=sys.stderr)
        return 1

    queue_mod.save_queue(queue, paths.queue)
    _emit({"id": args.id, "em": args.at, "preview": str(preview.relative_to(paths.root))})
    return 0


def cmd_host_media(args: argparse.Namespace) -> int:
    paths = _paths(args)
    queue = queue_mod.load_queue(paths.queue)
    targets = [
        item
        for item in queue["items"]
        if item.get("status") == "prepared" and (not args.ids or item["tiktok_id"] in set(args.ids))
    ]
    if not targets:
        _emit({"hospedados": [], "nota": "nenhum item em 'prepared'"})
        return 0

    # Mesmo motivo do cmd_prepare: hospedar le a duracao do arquivo local, algo
    # que so acontece no Mac.
    from .media import validate_for_instagram

    slug = hosting.repo_slug()
    hosting.ensure_release(args.tag)
    hosted, failed = [], []
    for item in targets:
        source = paths.ready(item["tiktok_id"])
        try:
            media = hosting.upload_asset(source, args.tag, slug=slug)
        except hosting.HostingError as error:
            failed.append({"id": item["tiktok_id"], "reason": str(error)})
            continue
        media["local_path"] = str(source.relative_to(paths.root))
        media["duration_seconds"] = validate_for_instagram(source)["media"]["duration_seconds"]
        queue_mod.transition(item, "hosted", by="local", note=f"asset em {args.tag}", media=media)
        queue_mod.transition(item, "scheduled", by="local", note="pronto para o cron publicar")
        hosted.append({"id": item["tiktok_id"], "url": media["asset_url"]})
        print(f"HOST_OK {item['tiktok_id']}", flush=True)

    queue_mod.save_queue(queue, paths.queue)
    _emit({"hospedados": hosted, "falhas": failed})
    return 1 if failed else 0


# ---------------------------------------------------------------------------
# Publishing
# ---------------------------------------------------------------------------


def cmd_publish_due(args: argparse.Namespace) -> int:
    paths = _paths(args)
    result = publisher_mod.publish_due(
        paths.queue, paths, max_per_run=args.max_per_run, dry_run=args.dry_run
    )
    _emit(result)
    return 1 if result.get("failed") else 0


def cmd_next_due(args: argparse.Namespace) -> int:
    """Quanto falta para o proximo post. O CI usa isto para dormir ate a hora.

    Com ``--seconds-only`` imprime um numero cru, para o shell do workflow ler
    sem precisar de um parser de JSON: os segundos a dormir, ou -1 para "nada
    nesta janela, pode encerrar".
    """
    paths = _paths(args)
    queue = queue_mod.load_queue(paths.queue)
    horizon = timedelta(seconds=args.max_wait_seconds) if args.max_wait_seconds else None
    espera = queue_mod.seconds_until_due(queue, horizon=horizon)

    if args.seconds_only:
        print(-1 if espera is None else int(espera))
        return 0

    proximo = min(
        (item for item in queue["items"] if queue_mod._due_at(item)),
        key=lambda item: queue_mod._due_at(item),
        default=None,
    )
    _emit(
        {
            "segundos_ate_o_proximo": None if espera is None else int(espera),
            "vencido_agora": espera == 0,
            "dentro_do_horizonte": espera is not None,
            "proximo": None
            if proximo is None
            else {"id": proximo["id"], "scheduled_at": proximo.get("scheduled_at")},
        }
    )
    return 0


def cmd_bump(args: argparse.Namespace) -> int:
    """Poe um video no proximo horario, trocando com quem estava la."""
    paths = _paths(args)
    queue = queue_mod.load_queue(paths.queue)
    alvo = date.fromisoformat(args.date) if args.date else None
    try:
        resultado = planner.bump(
            queue, args.tiktok_id, date=alvo, dry_run=args.dry_run, force=args.force
        )
    except queue_mod.QueueError as error:
        print(str(error), file=sys.stderr)
        return 2
    if not args.dry_run:
        queue_mod.save_queue(queue, paths.queue)
    _emit(resultado)
    return 0


def cmd_repost(args: argparse.Namespace) -> int:
    """Enfileira de novo um video que ja foi ao ar, como item novo."""
    paths = _paths(args)
    queue = queue_mod.load_queue(paths.queue)
    try:
        resultado = planner.repost(queue, args.tiktok_id, dry_run=args.dry_run)
    except queue_mod.QueueError as error:
        print(str(error), file=sys.stderr)
        return 2
    if not args.dry_run:
        # A midia veio do item publicado, que e um registro historico do que foi
        # ao ar -- e por isso nao acompanha edicoes posteriores do arquivo. Foi
        # exatamente esse o caso que originou o comando: os videos tinham sido
        # cortados depois de publicados, e o repost herdou o tamanho antigo. O
        # `doctor --check-assets` acusa isso como "asset inacessivel", porque o
        # arquivo na Release nao tem mais os bytes que a fila afirma.
        novo = next(i for i in queue["items"] if i["id"] == resultado["id"])
        local = paths.ready(args.tiktok_id)
        if local.exists():
            from .media import inspect

            atual = inspect(local)
            novo["media"] = {
                **(novo.get("media") or {}),
                "sha256": hosting.sha256_of(local),
                "bytes": local.stat().st_size,
                "duration_seconds": atual["duration_seconds"],
            }
            resultado["midia_reconferida"] = atual["duration_seconds"]
        queue_mod.save_queue(queue, paths.queue)
        resultado["nota"] = "rode 'jayes reschedule' para encaixar nos horarios do pool"
    _emit(resultado)
    return 0


def cmd_reconcile(args: argparse.Namespace) -> int:
    paths = _paths(args)
    queue = queue_mod.load_queue(paths.queue)
    stuck = [item for item in queue["items"] if item.get("status") == "publishing"]
    if not stuck:
        _emit({"reconciliados": [], "nota": "nada preso em 'publishing'"})
        return 0
    resolved = publisher_mod.reconcile(queue, publisher_mod.build_publisher(), paths)
    queue_mod.save_queue(queue, paths.queue)
    _emit({"reconciliados": [{"id": item["id"], "status": item["status"]} for item in resolved]})
    return 0


def cmd_refresh_token(args: argparse.Namespace) -> int:
    """Estende o token por mais 60 dias.

    Sem isso a automacao tem prazo de validade: o token do Painel de Apps vence
    em 60 dias e, passado o prazo, nao ha como renovar -- so gerar outro a mao.
    Este comando existe para que um cron mensal mantenha o relogio sempre longe
    do fim.
    """
    try:
        publisher = publisher_mod.build_publisher()
    except RuntimeError as error:
        print(str(error), file=sys.stderr)
        return 2

    try:
        resposta = publisher.refresh_long_lived_token()
    except Exception as error:  # noqa: BLE001 - qualquer falha aqui e operacional
        print(f"ERRO ao renovar o token: {error}", file=sys.stderr)
        return 1

    novo = resposta.get("access_token")
    if not novo:
        print(f"A Meta nao devolveu um token novo: {resposta}", file=sys.stderr)
        return 1

    dias = int(resposta.get("expires_in") or 0) // 86400
    # anota a validade: e a unica fonte que o doctor tem para avisar antes da
    # hora, ja que a Graph API do Instagram nao expoe 'debug_token'
    if dias:
        agora = datetime.now(UTC)
        _paths(args).token_estado.write_text(
            json.dumps(
                {
                    "renovado_em": agora.isoformat(),
                    "expira_em": (agora + timedelta(days=dias)).isoformat(),
                    "validade_dias": dias,
                    "origem": "refresh-token",
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    # O token so aparece em stdout quando pedido: o log do Actions e publico
    # neste repositorio, e um token vazado vale ate ser revogado a mao.
    if args.print_token:
        print(novo)
    else:
        _emit({"renovado": True, "validade_dias": dias, "token": f"...{novo[-6:]}"})
    return 0


def cmd_check_instagram(args: argparse.Namespace) -> int:
    try:
        publisher = publisher_mod.build_publisher()
    except RuntimeError as error:
        print(str(error), file=sys.stderr)
        return 2
    _emit(
        {
            "conta": publisher.check_account(),
            "quota": publisher.content_publishing_limit(),
        }
    )
    return 0


def cmd_requeue(args: argparse.Namespace) -> int:
    """Devolve itens 'failed' para a fila.

    O caminho de recuperacao ja estava documentado mas nao tinha comando, o que
    empurrava para edicao manual do JSON -- justamente o que nunca se deve fazer,
    porque um item reaberto errado vira post duplicado.
    """
    paths = _paths(args)
    queue = queue_mod.load_queue(paths.queue)
    alvos = [
        item
        for item in queue["items"]
        if item.get("status") == "failed"
        and (not args.ids or str(item.get("tiktok_id")) in set(args.ids))
    ]
    if not alvos:
        _emit({"requeued": [], "nota": "nenhum item em 'failed'"})
        return 0

    for item in alvos:
        if not args.dry_run:
            queue_mod.transition(
                item, "scheduled", by="local", note=args.note or "requeue manual", attempts=0
            )
    if not args.dry_run:
        queue_mod.save_queue(queue, paths.queue)
    _emit(
        {
            "requeued": [
                {
                    "id": i["tiktok_id"],
                    "quando": i["scheduled_at"],
                    "erro_anterior": i.get("last_error"),
                }
                for i in alvos
            ],
            "dry_run": args.dry_run,
        }
    )
    return 0


def cmd_mark_trials(args: argparse.Namespace) -> int:
    paths = _paths(args)
    queue = queue_mod.load_queue(paths.queue)
    try:
        resultado = planner.mark_trials(
            queue, clear=args.clear, limit_days=args.limit_days, force=args.force
        )
    except queue_mod.QueueError as error:
        print(f"ERRO: {error}", file=sys.stderr)
        return 1

    if (resultado["marcados"] or resultado["limpos"]) and not args.dry_run:
        queue_mod.save_queue(queue, paths.queue)
    resultado["dry_run"] = args.dry_run
    resultado["marcados"] = len(resultado["marcados"])
    resultado["limpos"] = len(resultado["limpos"])
    _emit(resultado)
    return 0


def cmd_collect_insights(args: argparse.Namespace) -> int:
    paths = _paths(args)
    queue = queue_mod.load_queue(paths.queue)
    try:
        publisher = publisher_mod.build_publisher()
    except RuntimeError as error:
        print(str(error), file=sys.stderr)
        return 2
    _emit(insights.collect(queue, publisher, paths.insights_csv, dry_run=args.dry_run))
    return 0


def cmd_audience(args: argparse.Namespace) -> int:
    """Quem segue e quando esta online. Só leitura, para o dado ser reproduzível."""
    try:
        publisher = publisher_mod.build_publisher()
    except RuntimeError as error:
        print(str(error), file=sys.stderr)
        return 2

    _emit(
        {
            "conta": publisher.check_account(),
            "idade_genero": publisher.follower_demographics("age,gender"),
            "cidades": dict(list(publisher.follower_demographics("city").items())[:10]),
            "seguidores_online_por_hora": publisher.online_followers(),
            "nota": (
                "o fuso das chaves horarias nao e documentado pela Meta; o corte de dia "
                "da API e UTC-7, o que sugere que as chaves tambem sao. Trate como indicio."
            ),
        }
    )
    return 0


# ---------------------------------------------------------------------------
# Inspection
# ---------------------------------------------------------------------------


def cmd_status(args: argparse.Namespace) -> int:
    paths = _paths(args)
    queue = queue_mod.load_queue(paths.queue)
    due = queue_mod.find_due(queue)
    upcoming = sorted(
        (item for item in queue["items"] if item.get("status") in {"scheduled", "retry"}),
        key=lambda item: str(item.get("scheduled_at")),
    )
    _emit(
        {
            "fila": {
                "total": len(queue["items"]),
                "por_status": queue_mod.counts_by_status(queue),
                "vencidos_agora": [item["id"] for item in due],
                "proximo": (
                    {"id": upcoming[0]["id"], "quando": upcoming[0]["scheduled_at"]}
                    if upcoming
                    else None
                ),
            },
            "publicados_24h": queue_mod.published_last_24h(paths.publish_log),
            "orcamento_24h_restante": queue_mod.daily_budget_left(paths.publish_log),
            "acervo": {
                "baixados": len(
                    [line for line in paths.downloaded.read_text().splitlines() if line.strip()]
                )
                if paths.downloaded.exists()
                else 0,
                "preparados": len(list(paths.ready_dir.glob("*.mp4")))
                if paths.ready_dir.exists()
                else 0,
                "legendas_aprovadas": sum(
                    1
                    for path in paths.captions_dir.glob("*.json")
                    if (captions_mod.load(path) or {}).get("status") == "approved"
                )
                if paths.captions_dir.exists()
                else 0,
            },
            "ambiente": {
                "instagram_user_id": bool(os.environ.get("INSTAGRAM_USER_ID")),
                "instagram_token": bool(os.environ.get("INSTAGRAM_ACCESS_TOKEN")),
                "anthropic_key": bool(os.environ.get("ANTHROPIC_API_KEY")),
                "publicacao_ligada": publisher_mod.publishing_enabled(),
            },
        }
    )
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    """Preflight: everything that must hold before the cron is allowed to post."""
    paths = _paths(args)
    problems: list[str] = []
    notes: dict[str, Any] = {}

    try:
        queue = queue_mod.load_queue(paths.queue)
    except queue_mod.QueueError as error:
        _emit({"ok": False, "problemas": [str(error)]})
        return 1

    stuck = [item["id"] for item in queue["items"] if item.get("status") == "publishing"]
    if stuck:
        problems.append(f"{len(stuck)} item(ns) preso(s) em 'publishing': {', '.join(stuck)}")

    scheduled = [item for item in queue["items"] if item.get("status") in {"scheduled", "retry"}]
    notes["itens_agendados"] = len(scheduled)
    for item in scheduled:
        if not (item.get("caption") or "").strip():
            problems.append(f"{item['id']}: sem legenda congelada")
        media = item.get("media") or {}
        # Carrossel guarda um asset por slide em media.assets; os outros tipos
        # guardam um asset_url so. Exigir asset_url de todos reprovaria todo
        # carrossel valido.
        kind = publisher_mod.media_kind(item)
        if kind == "carousel":
            slides = media.get("assets") or []
            if not slides:
                problems.append(f"{item['id']}: carrossel sem media.assets")
            elif not 2 <= len(slides) <= 10:
                problems.append(
                    f"{item['id']}: carrossel com {len(slides)} slides (a Meta aceita 2 a 10)"
                )
            elif any(not (s.get("asset_url") if isinstance(s, dict) else s) for s in slides):
                problems.append(f"{item['id']}: carrossel com slide sem asset_url")
        elif not media.get("asset_url"):
            problems.append(f"{item['id']}: sem asset_url (rode 'jayes host-media')")

    times = [str(item.get("scheduled_at")) for item in scheduled]
    if len(times) != len(set(times)):
        problems.append("ha itens agendados para o mesmo horario")

    if args.check_assets:
        # Contado e reportado de proposito: um "ok" silencioso nao distingue
        # "conferi os 26" de "nao conferi nenhum".
        verified = 0
        for item in scheduled:
            media = item.get("media") or {}
            # um carrossel tem N assets para conferir, nao um
            if publisher_mod.media_kind(item) == "carousel":
                for n, slide in enumerate(media.get("assets") or [], start=1):
                    url = slide.get("asset_url") if isinstance(slide, dict) else slide
                    if not url:
                        continue
                    esperado = slide.get("bytes") if isinstance(slide, dict) else None
                    check = hosting.verify_asset(url, expected_bytes=esperado)
                    verified += 1
                    if not check["ok"]:
                        problems.append(
                            f"{item['id']}: slide {n} inacessivel ({check.get('status')})"
                        )
                continue
            if not media.get("asset_url"):
                continue
            check = hosting.verify_asset(media["asset_url"], expected_bytes=media.get("bytes"))
            verified += 1
            if not check["ok"]:
                problems.append(f"{item['id']}: asset inacessivel ({check.get('status')})")
        notes["assets_verificados"] = verified

    if publisher_mod.publishing_enabled():
        try:
            publisher = publisher_mod.build_publisher()
            account = publisher.check_account()
            notes["conta"] = account.get("username")
            quota = publisher.content_publishing_limit()
            notes["quota"] = quota
            used = int(quota.get("quota_usage") or 0)
            if used >= queue_mod.DAILY_PUBLISH_LIMIT:
                problems.append(f"quota da Meta esgotada ({used})")
        except Exception as error:
            problems.append(f"nao consegui falar com a Graph API: {error}")
    else:
        notes["publicacao"] = "PUBLISH_ENABLED nao esta true (nada sera postado)"

    # Validade do token. Sem isto o doctor dizia "ok" com um dia de token
    # restante: ele conferia conta e quota, nunca prazo. Quando o token vence, a
    # publicacao para e nada avisa -- nem aqui, nem no app.
    estado = paths.token_estado
    if not estado.exists():
        problems.append(
            f"{estado.name} nao existe: ninguem sabe quando o token vence. "
            "Rode 'jayes refresh-token'."
        )
    else:
        dados = json.loads(estado.read_text(encoding="utf-8"))
        expira = datetime.fromisoformat(dados["expira_em"])
        renovado = datetime.fromisoformat(dados["renovado_em"])
        agora = datetime.now(UTC)
        faltam = (expira - agora).days
        notes["token_expira_em_dias"] = faltam
        notes["token_renovado_em"] = dados["renovado_em"][:10]
        if faltam < TOKEN_ALERTA_DIAS:
            problems.append(
                f"o token vence em {faltam} dia(s) ({dados['expira_em'][:10]}). "
                "Sem renovacao a publicacao para em silencio."
            )
        # O cron de renovacao roda todo dia 1. Se a ultima renovacao tem mais de
        # 45 dias, ele nao esta rodando -- provavelmente falta o SECRETS_PAT.
        if (agora - renovado).days > TOKEN_RENOVACAO_MAX_DIAS:
            problems.append(
                f"a ultima renovacao foi ha {(agora - renovado).days} dias; o cron mensal "
                "nao esta rodando. Confira o secret SECRETS_PAT e o workflow token.yml."
            )

    notes["orcamento_24h_restante"] = queue_mod.daily_budget_left(paths.publish_log)
    _emit({"ok": not problems, "problemas": problems, "notas": notes})
    return 1 if problems else 0


def cmd_migrate_queue(args: argparse.Namespace) -> int:
    paths = _paths(args)
    if not paths.queue.exists():
        queue_mod.save_queue({"version": queue_mod.SCHEMA_VERSION, "items": []}, paths.queue)
        _emit({"criada": True, "itens": 0})
        return 0

    raw = json.loads(paths.queue.read_text(encoding="utf-8"))
    if raw.get("version") == queue_mod.SCHEMA_VERSION:
        _emit({"ja_migrada": True, "itens": len(raw.get("items") or [])})
        return 0

    backup = paths.queue.with_suffix(".v1.json")
    backup.write_text(json.dumps(raw, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    migrated = queue_mod.migrate_v1(raw)
    queue_mod.save_queue(migrated, paths.queue)
    _emit(
        {
            "migrados": len(migrated["items"]),
            "backup": str(backup),
            "por_status": queue_mod.counts_by_status(migrated),
        }
    )
    return 0


def cmd_init_slots(args: argparse.Namespace) -> int:
    paths = _paths(args)
    if paths.slots.exists() and not args.force:
        _emit({"ja_existe": str(paths.slots)})
        return 0
    scheduling.save_slots(scheduling.DEFAULT_CONFIG, paths.slots)
    _emit({"criado": str(paths.slots), "slots": len(scheduling.DEFAULT_SLOTS)})
    return 0


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

COMMANDS: dict[str, Callable[[argparse.Namespace], int]] = {
    "audit-tiktok": cmd_audit_tiktok,
    "download-archive": cmd_download_archive,
    "draft-captions": cmd_draft_captions,
    "import-captions": cmd_import_captions,
    "review-captions": cmd_review_captions,
    "approve-caption": cmd_approve_caption,
    "prepare": cmd_prepare,
    "plan-queue": cmd_plan_queue,
    "refresh-captions": cmd_refresh_captions,
    "reschedule": cmd_reschedule,
    "pick-covers": cmd_pick_covers,
    "set-cover": cmd_set_cover,
    "host-media": cmd_host_media,
    "publish-due": cmd_publish_due,
    "next-due": cmd_next_due,
    "bump": cmd_bump,
    "repost": cmd_repost,
    "reconcile": cmd_reconcile,
    "refresh-token": cmd_refresh_token,
    "check-instagram": cmd_check_instagram,
    "mark-trials": cmd_mark_trials,
    "requeue": cmd_requeue,
    "collect-insights": cmd_collect_insights,
    "audience": cmd_audience,
    "status": cmd_status,
    "doctor": cmd_doctor,
    "migrate-queue": cmd_migrate_queue,
    "init-slots": cmd_init_slots,
    "adaptar-es": cmd_adaptar_es,
    "importar-legendas-es": cmd_importar_legendas_es,
    "plan-es": cmd_plan_es,
    "rotina-status": cmd_rotina_status,
    "remesclar": cmd_remesclar,
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="jayes")
    parser.add_argument("--root", type=Path, help="Raiz do projeto (default: o repositorio)")
    commands = parser.add_subparsers(dest="command", required=True)

    audit = commands.add_parser("audit-tiktok", help="Inventaria o perfil e ranqueia os videos")
    audit.add_argument("--input", type=Path, help="Reaproveita um inventario ja salvo")
    audit.add_argument("--output", type=Path)

    archive = commands.add_parser("download-archive", help="Baixa os videos que faltam")
    archive.add_argument("--inventory", type=Path)
    archive.add_argument("--output", type=Path)
    archive.add_argument("--archive", type=Path)
    archive.add_argument("--errors", type=Path)
    archive.add_argument("--limit", type=int)
    archive.add_argument("--sleep", type=float, default=4.0, help="Pausa entre downloads")

    drafts = commands.add_parser("draft-captions", help="Gera legendas com IA (local)")
    drafts.add_argument("--top", type=int, help="Apenas os N melhores do ranking")
    drafts.add_argument("--ids", nargs="+")
    drafts.add_argument("--force", action="store_true", help="Regera mesmo se o cache bater")

    imports = commands.add_parser(
        "import-captions", help="Carrega legendas escritas a mao (sem precisar de API de IA)"
    )
    imports.add_argument("--file", type=Path, required=True, help="JSON com as legendas")
    imports.add_argument(
        "--approve", action="store_true", help="Aprova as que passarem no validador"
    )
    imports.add_argument("--author", default="humano", help="Quem escreveu (vai para o registro)")

    review = commands.add_parser("review-captions", help="Mostra as legendas para revisao")
    review.add_argument("--status", choices=["draft", "approved"])

    approve = commands.add_parser("approve-caption", help="Aprova legendas para agendamento")
    approve.add_argument("--ids", nargs="+")
    approve.add_argument("--force", action="store_true", help="Aprova apesar dos avisos")

    prepare = commands.add_parser("prepare", help="Normaliza o video para o formato de Reels")
    prepare.add_argument("--id", nargs="+")
    prepare.add_argument("--all-approved", action="store_true")
    prepare.add_argument("--force", action="store_true")

    plan = commands.add_parser(
        "plan-queue", help="Agenda os videos elegiveis nos melhores horarios"
    )
    plan.add_argument("--days", type=int, default=14)
    # Sem default fixo: a fonte da verdade e "posts_per_day" em data/slots.json.
    # Um 2 cravado aqui ignorava calado a configuracao -- foi o que fez o
    # reschedule pedir 83 horarios para 161 itens depois da mudanca para 1/dia.
    plan.add_argument("--per-day", type=int, help="Sobrepoe o posts_per_day da configuracao")
    plan.add_argument("--start", help="Data inicial (YYYY-MM-DD)")
    plan.add_argument("--strategy", choices=["front-loaded", "interleaved"], default="front-loaded")
    plan.add_argument("--dry-run", action="store_true")

    redata = commands.add_parser(
        "reschedule", help="Redistribui os itens pendentes sobre o pool de horarios atual"
    )
    redata.add_argument("--per-day", type=int, help="Sobrepoe o posts_per_day da configuracao")
    redata.add_argument("--dry-run", action="store_true")

    refresh = commands.add_parser(
        "refresh-captions", help="Recopia as legendas aprovadas para itens ainda nao publicados"
    )
    refresh.add_argument("--ids", nargs="+")
    refresh.add_argument("--dry-run", action="store_true")

    picks = commands.add_parser(
        "pick-covers", help="Escolhe a capa (thumb_offset) do quadro mais nitido"
    )
    picks.add_argument("--ids", nargs="+")
    picks.add_argument("--force", action="store_true", help="Refaz mesmo se ja houver capa")

    setcover = commands.add_parser("set-cover", help="Fixa a capa num instante escolhido a mao")
    setcover.add_argument("--id", required=True, help="tiktok_id do video")
    setcover.add_argument("--at", required=True, type=float, help="Instante em segundos")

    host = commands.add_parser("host-media", help="Sobe os videos como assets de Release")
    host.add_argument("--tag", default="media-v1")
    host.add_argument("--ids", nargs="+")

    publish = commands.add_parser("publish-due", help="Publica o que estiver vencido (CI)")
    publish.add_argument("--max-per-run", type=int, default=1)
    publish.add_argument("--dry-run", action="store_true")

    proximo = commands.add_parser(
        "next-due", help="Quanto falta para o proximo post (o CI dorme por este numero)"
    )
    proximo.add_argument(
        "--max-wait-seconds",
        type=int,
        default=0,
        help="Acima disto responde 'nada nesta janela'. 0 desliga o corte.",
    )
    proximo.add_argument(
        "--seconds-only",
        action="store_true",
        help="Imprime so o numero de segundos (-1 = nada a esperar), para o shell do CI",
    )

    bump = commands.add_parser(
        "bump", help="Adianta um video para o proximo horario (trocando com quem estava la)"
    )
    bump.add_argument("tiktok_id")
    bump.add_argument("--date", help="Data de destino (YYYY-MM-DD); default: o proximo post")
    bump.add_argument("--dry-run", action="store_true")
    bump.add_argument("--force", action="store_true", help="Aceita horario de destino ja vencido")

    repost = commands.add_parser(
        "repost", help="Enfileira de novo um video ja publicado, como item novo"
    )
    repost.add_argument("tiktok_id")
    repost.add_argument("--dry-run", action="store_true")

    commands.add_parser("reconcile", help="Resolve itens presos em 'publishing'")
    token = commands.add_parser("refresh-token", help="Estende o token por mais 60 dias")
    token.add_argument(
        "--print-token",
        action="store_true",
        help="Imprime o token puro em stdout, para o CI gravar no secret",
    )

    commands.add_parser("check-instagram", help="Testa o token e mostra a quota")

    requeue = commands.add_parser("requeue", help="Devolve itens 'failed' para a fila")
    requeue.add_argument("--ids", nargs="+", help="tiktok_ids; sem isso, todos os falhados")
    requeue.add_argument("--note", help="Motivo, gravado no historico do item")
    requeue.add_argument("--dry-run", action="store_true")

    trials = commands.add_parser(
        "mark-trials", help="Marca 1 dos 2 posts do dia como reel de teste (so nao seguidores)"
    )
    trials.add_argument("--dry-run", action="store_true")
    trials.add_argument("--clear", action="store_true", help="Remove todas as marcas")
    trials.add_argument(
        "--force", action="store_true", help="Marca mesmo sem a permissao confirmada"
    )
    trials.add_argument(
        "--limit-days", type=int, help="Marca so os N primeiros dias (liberacao em etapas)"
    )

    coleta = commands.add_parser(
        "collect-insights", help="Coleta metricas dos Reels publicados (24h e 7 dias)"
    )
    coleta.add_argument("--dry-run", action="store_true", help="Mostra o que coletaria")

    commands.add_parser("audience", help="Quem segue o perfil e quando esta online")
    commands.add_parser("status", help="Panorama da fila e do acervo")
    commands.add_parser("migrate-queue", help="Converte a fila do schema v1 para v2")

    doctor = commands.add_parser("doctor", help="Checagem completa antes de publicar")
    doctor.add_argument("--check-assets", action="store_true", help="Confere cada URL de midia")

    adaptar = commands.add_parser(
        "adaptar-es", help="Reedita a midia com portugues queimado (data/adaptacoes-es.json)"
    )
    adaptar.add_argument("--acervo", type=Path, help="Raiz do projeto de analise")
    adaptar.add_argument("--refazer", action="store_true", help="Reencoda mesmo se ja existe")

    importar = commands.add_parser(
        "importar-legendas-es", help="Carrega legendas em espanhol escritas a mao"
    )
    importar.add_argument("--file", type=Path, required=True, help="JSON com as legendas")
    importar.add_argument("--acervo", type=Path, help="Raiz do projeto de analise")
    importar.add_argument("--autor", default="claude-opus-5-sessao")
    importar.add_argument(
        "--aprovar", action="store_true", help="Aprova ja, se passar no validador"
    )

    planes = commands.add_parser("plan-es", help="Monta a fila a partir do acervo em espanhol")
    planes.add_argument("--quantidade", type=int, default=8)
    planes.add_argument(
        "--alvo-video", type=float, help="Proporcao de video (default: deduz do estoque)"
    )
    planes.add_argument("--acervo", type=Path, help="Raiz do projeto de analise")
    planes.add_argument("--dry-run", action="store_true", help="Nao sobe midia nem grava a fila")

    remescla = commands.add_parser(
        "remesclar", help="Alterna video e estatico na fila sem mudar os horarios"
    )
    remescla.add_argument("--alvo-video", type=float)
    remescla.add_argument("--dry-run", action="store_true")

    rotina = commands.add_parser(
        "rotina-status", help="A rotina horaria de legendas precisa trabalhar agora?"
    )
    rotina.add_argument("--folga-dias", type=float, default=6.0, help="Fila alvo, em dias")
    rotina.add_argument("--quantos", type=int, default=2, help="Quantos candidatos sugerir")
    rotina.add_argument("--acervo", type=Path, help="Raiz do projeto de analise")

    slots = commands.add_parser("init-slots", help="Cria data/slots.json com os horarios padrao")
    slots.add_argument("--force", action="store_true")

    return parser


def main(argv: list[str] | None = None) -> int:
    # .env.local vem primeiro e vence: e a convencao que a maioria das
    # ferramentas usa para o arquivo de segredos de uma maquina so. Sem isso o
    # token fica num arquivo que ninguem le, e o erro que aparece e "variavel
    # ausente" -- que manda procurar no lugar errado.
    load_dotenv(".env.local")
    load_dotenv()
    args = build_parser().parse_args(argv)
    try:
        return COMMANDS[args.command](args)
    except (queue_mod.QueueError, captions_mod.CaptionError, hosting.HostingError) as error:
        print(f"ERRO: {error}", file=sys.stderr)
        return 1
    except FileNotFoundError as error:
        print(f"ERRO: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
