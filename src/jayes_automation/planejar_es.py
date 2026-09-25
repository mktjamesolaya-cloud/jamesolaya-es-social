"""Transforma candidatos em itens de fila: hospeda a midia e agenda.

Fica separado do ``planner.py`` herdado de proposito. Aquele casa video do
TikTok com horario e assume um arquivo por post; aqui um post pode ter dez
arquivos, e a ordem entre eles importa.

O caminho e sempre o mesmo:

    candidatos -> mescla -> horarios -> hospedagem -> itens 'scheduled'

A hospedagem vem **depois** do corte por quantidade, nunca antes: subir 685
arquivos para descobrir que so 20 seriam usados desperdicaria banda e encheria
a Release de lixo que ninguem apaga.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from . import hosting, mescla, scheduling
from . import queue as queue_mod
from .ingest import Candidato

#: Tag da Release onde a midia vive. Uma so: o GitHub aceita milhares de assets
#: por release e versionar por lote so dificultaria achar as coisas.
TAG_MIDIA = "media-v1"


def _nome_asset(c: Candidato, arquivo: Path, indice: int) -> str:
    """Nome unico e legivel dentro da Release.

    Precisa ser unico porque todos os assets dividem o mesmo namespace, e
    legivel porque e o que aparece na URL publica -- e essa URL vai para a
    Meta, que a registra nos logs dela.
    """
    if c.media_kind == "carousel":
        return f"{c.short_code}-{indice + 1:02d}{arquivo.suffix}"
    return f"{c.short_code}{arquivo.suffix}"


def hospedar(
    c: Candidato,
    *,
    tag: str = TAG_MIDIA,
    upload: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Sobe os arquivos do candidato e devolve o bloco ``media`` da fila.

    ``upload`` existe para os testes substituirem a chamada ao ``gh``.
    """
    upload = upload or hosting.upload_asset
    if not c.arquivos:
        raise ValueError(f"{c.pasta}: nada para hospedar")

    enviados = []
    for i, arquivo in enumerate(c.arquivos):
        destino = arquivo
        nome = _nome_asset(c, arquivo, i)
        # o gh usa o nome do arquivo em disco como nome do asset; copiar para um
        # nome unico evita colisao entre posts que tem 'imagem.jpg' igual
        if arquivo.name != nome:
            destino = arquivo.parent / nome
            if not destino.exists():
                destino.write_bytes(arquivo.read_bytes())
        info = upload(destino, tag)
        info["is_video"] = destino.suffix.lower() in {".mp4", ".mov"}
        enviados.append(info)

    if c.media_kind == "carousel":
        return {"kind": "carousel", "assets": enviados}

    principal = enviados[0]
    media: dict[str, Any] = {
        "kind": c.media_kind,
        "asset_url": principal["asset_url"],
        "asset_name": principal["asset_name"],
        "release_tag": principal["release_tag"],
        "sha256": principal["sha256"],
        "bytes": principal["bytes"],
    }
    return media


def montar_fila(
    candidatos: Sequence[Candidato],
    config_slots: dict[str, Any],
    *,
    quantidade: int,
    alvo_video: float | None = None,
    legendas: dict[str, str] | None = None,
    nao_antes: datetime | None = None,
    ocupados: Sequence[datetime] = (),
    upload: Callable[..., dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Monta ``quantidade`` itens prontos para publicar.

    ``legendas`` mapeia short_code -> legenda em espanhol ja aprovada. Item sem
    legenda aprovada **nao entra na fila**: publicar a legenda em portugues no
    perfil espanhol seria pior que nao publicar.
    """
    legendas = legendas or {}
    por_dia = int(config_slots.get("posts_per_day", 2))

    # o alvo sai do estoque, nao do gosto: e a proporcao em que video e
    # estatico acabam no mesmo dia
    n_video = sum(1 for c in candidatos if c.media_kind == "reel")
    if alvo_video is None:
        alvo_video = mescla.sugerir_alvo(n_video, len(candidatos) - n_video, por_dia)

    ordenados = mescla.intercalar(
        [{"media_kind": c.media_kind, "c": c} for c in candidatos], alvo_video
    )
    escolhidos = [x["c"] for x in ordenados]

    # so entra quem tem legenda aprovada
    prontos = [c for c in escolhidos if legendas.get(c.short_code)]
    sem_legenda = len(escolhidos) - len(prontos)
    prontos = prontos[:quantidade]

    # plan_slots trabalha em dias, nao em quantidade: +2 de folga porque o
    # jitter e o intervalo minimo podem descartar horarios no meio do caminho
    tz = ZoneInfo(config_slots.get("timezone", scheduling.TIMEZONE))
    inicio = (nao_antes or datetime.now(tz)).astimezone(tz)
    dias = max(1, -(-len(prontos) // max(por_dia, 1)) + 2)
    horarios = scheduling.plan_slots(
        inicio.date(), dias, config_slots, ocupados, per_day=por_dia, not_before=inicio
    )[: len(prontos)]
    if len(horarios) < len(prontos):
        prontos = prontos[: len(horarios)]

    itens: list[dict[str, Any]] = []
    for c, slot in zip(prontos, horarios, strict=False):
        item = queue_mod.new_item(
            tiktok_id=c.short_code,
            source_url=f"https://www.instagram.com/p/{c.short_code}/",
            scheduled_at=slot.scheduled_at,
            scheduled_at_utc=slot.scheduled_at_utc,
            slot_id=slot.slot_id,
            rank=c.posicao,
        )
        item["media"] = hospedar(c, upload=upload)
        # Copia, nao referencia: editar a legenda depois nao pode mexer num
        # post que ja esta no calendario.
        item["caption"] = legendas[c.short_code]
        item["caption_fingerprint"] = hashlib.sha256(
            item["caption"].encode("utf-8")
        ).hexdigest()[:16]
        item["origem"] = {
            "pasta": c.pasta,
            "formato": c.formato,
            "editorial": c.editorial,
            "balde": c.balde,
            "score": c.score,
            "observacoes": c.observacoes,
        }
        # planned -> prepared -> hosted -> scheduled, respeitando a maquina de estados
        for destino in ("prepared", "hosted", "scheduled"):
            queue_mod.transition(item, destino, by="local")
        itens.append(item)

    resumo = {
        "montados": len(itens),
        "alvo_video": round(alvo_video, 4),
        "sem_legenda_aprovada": sem_legenda,
        "candidatos_disponiveis": len(candidatos),
        **mescla.resumir([{"media_kind": i["media"]["kind"]} for i in itens], por_dia),
    }
    return itens, resumo
