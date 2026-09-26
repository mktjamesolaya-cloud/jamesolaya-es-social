"""Montagem da fila: hospedagem, mescla, agendamento e a trava da legenda.

Nenhum teste sobe arquivo de verdade -- ``upload`` e substituido. O que se
verifica e o formato do bloco ``media`` que sai, porque e ele que o publisher
consome e a Meta valida.
"""

import json
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from jayes_automation import planejar_es
from jayes_automation.ingest import Candidato

TZ = ZoneInfo("America/Mexico_City")

SLOTS = {
    "version": 1,
    "timezone": "America/Mexico_City",
    "posts_per_day": 2,
    "min_gap_minutes": 300,
    "jitter_minutes": 0,
    "explore_every": 0,
    "pool": [
        {"id": "meio-dia", "weekdays": [0, 1, 2, 3, 4, 5, 6], "time": "12:00", "weight": 1.0},
        {"id": "noite", "weekdays": [0, 1, 2, 3, 4, 5, 6], "time": "18:00", "weight": 1.0},
    ],
}


def upload_falso(path: Path, tag: str, **kw):
    """Devolve o mesmo formato de hosting.upload_asset, sem tocar no gh."""
    return {
        "release_tag": tag,
        "asset_name": path.name,
        "asset_url": f"https://github.com/o/r/releases/download/{tag}/{path.name}",
        "sha256": "abc123",
        "bytes": 100,
    }


def cand(tmp_path, short_code, kind, *, n_arquivos=1, score=10.0, posicao=1):
    pasta = tmp_path / f"{posicao:03d}_{short_code}"
    pasta.mkdir(parents=True, exist_ok=True)
    if kind == "reel":
        nomes = ["video.mp4"]
    elif kind == "carousel":
        nomes = [f"slide-{i:02d}.jpg" for i in range(1, n_arquivos + 1)]
    else:
        nomes = ["imagem.jpg"]
    arquivos = []
    for nome in nomes:
        p = pasta / nome
        p.write_bytes(b"x" * 100)
        arquivos.append(p)
    return Candidato(
        short_code=short_code, posicao=posicao, pasta=pasta.name, media_kind=kind,
        arquivos=arquivos, capa=None, legenda_original="original em portugues",
        score=score, formato={"reel": "Reel", "carousel": "Carrossel"}.get(kind, "Imagem única"),
        editorial="Aula técnica", balde="A", data="2026-01-01",
    )


def agora():
    return datetime(2026, 10, 1, 8, 0, tzinfo=TZ)


# --- hospedagem -----------------------------------------------------------

def test_imagem_gera_bloco_media_com_asset_url(tmp_path):
    c = cand(tmp_path, "IMG1", "image")
    media = planejar_es.hospedar(c, upload=upload_falso)
    assert media["kind"] == "image"
    assert media["asset_url"].endswith("IMG1.jpg")
    assert "assets" not in media


def test_carrossel_gera_lista_de_assets_na_ordem(tmp_path):
    c = cand(tmp_path, "CAR1", "carousel", n_arquivos=3)
    media = planejar_es.hospedar(c, upload=upload_falso)
    assert media["kind"] == "carousel"
    nomes = [a["asset_name"] for a in media["assets"]]
    assert nomes == ["CAR1-01.jpg", "CAR1-02.jpg", "CAR1-03.jpg"]


def test_nome_do_asset_e_unico_por_post(tmp_path):
    """Dois posts com 'imagem.jpg' colidiriam no namespace da Release."""
    a = planejar_es.hospedar(cand(tmp_path, "AAA", "image", posicao=1), upload=upload_falso)
    b = planejar_es.hospedar(cand(tmp_path, "BBB", "image", posicao=2), upload=upload_falso)
    assert a["asset_name"] != b["asset_name"]


def test_video_e_marcado_como_video_no_carrossel(tmp_path):
    c = cand(tmp_path, "MIX", "carousel", n_arquivos=2)
    c.arquivos[1] = c.arquivos[1].parent / "slide-02.mp4"
    c.arquivos[1].write_bytes(b"x" * 100)
    media = planejar_es.hospedar(c, upload=upload_falso)
    assert media["assets"][0]["is_video"] is False
    assert media["assets"][1]["is_video"] is True


def test_candidato_sem_arquivo_falha(tmp_path):
    c = cand(tmp_path, "X", "image")
    c.arquivos = []
    with pytest.raises(ValueError, match="nada para hospedar"):
        planejar_es.hospedar(c, upload=upload_falso)


# --- a trava da legenda ---------------------------------------------------

def test_item_sem_legenda_em_espanhol_nao_entra_na_fila(tmp_path):
    """Publicar legenda em portugues no perfil espanhol seria pior que nao publicar."""
    cs = [cand(tmp_path, f"C{i}", "image", posicao=i) for i in range(1, 4)]
    itens, resumo = planejar_es.montar_fila(
        cs, SLOTS, quantidade=3, legendas={"C2": "hola"},
        nao_antes=agora(), upload=upload_falso,
    )
    assert len(itens) == 1
    assert itens[0]["tiktok_id"] == "C2"
    assert resumo["sem_legenda_aprovada"] == 2


def test_sem_nenhuma_legenda_a_fila_sai_vazia(tmp_path):
    cs = [cand(tmp_path, f"C{i}", "image", posicao=i) for i in range(1, 4)]
    itens, resumo = planejar_es.montar_fila(
        cs, SLOTS, quantidade=3, legendas={}, nao_antes=agora(), upload=upload_falso
    )
    assert itens == []
    assert resumo["sem_legenda_aprovada"] == 3


def test_legenda_e_congelada_com_fingerprint(tmp_path):
    cs = [cand(tmp_path, "C1", "image")]
    itens, _ = planejar_es.montar_fila(
        cs, SLOTS, quantidade=1, legendas={"C1": "hola mundo"},
        nao_antes=agora(), upload=upload_falso,
    )
    assert itens[0]["caption"] == "hola mundo"
    assert len(itens[0]["caption_fingerprint"]) == 16


# --- agendamento ----------------------------------------------------------

def test_itens_saem_no_estado_scheduled(tmp_path):
    cs = [cand(tmp_path, "C1", "image")]
    itens, _ = planejar_es.montar_fila(
        cs, SLOTS, quantidade=1, legendas={"C1": "hola"},
        nao_antes=agora(), upload=upload_falso,
    )
    assert itens[0]["status"] == "scheduled"
    # passou por toda a maquina de estados, sem pular etapa
    estados = [h["to"] for h in itens[0]["history"]]
    assert estados == ["planned", "prepared", "hosted", "scheduled"]


def test_horarios_sao_distintos_e_no_fuso_do_mexico(tmp_path):
    cs = [cand(tmp_path, f"C{i}", "image", posicao=i) for i in range(1, 5)]
    legendas = {f"C{i}": "hola" for i in range(1, 5)}
    itens, _ = planejar_es.montar_fila(
        cs, SLOTS, quantidade=4, legendas=legendas, nao_antes=agora(), upload=upload_falso
    )
    horarios = [i["scheduled_at"] for i in itens]
    assert len(set(horarios)) == len(horarios), "agendou dois posts no mesmo horario"
    for h in horarios:
        assert datetime.fromisoformat(h).hour in (12, 18)


def test_nao_agenda_no_passado(tmp_path):
    cs = [cand(tmp_path, f"C{i}", "image", posicao=i) for i in range(1, 3)]
    legendas = {f"C{i}": "hola" for i in range(1, 3)}
    momento = datetime(2026, 10, 1, 14, 0, tzinfo=TZ)  # depois do slot das 12h
    itens, _ = planejar_es.montar_fila(
        cs, SLOTS, quantidade=2, legendas=legendas, nao_antes=momento, upload=upload_falso
    )
    for i in itens:
        assert datetime.fromisoformat(i["scheduled_at"]) >= momento


# --- mescla ---------------------------------------------------------------

def test_a_fila_sai_mesclada_e_nao_em_blocos(tmp_path):
    cs = ([cand(tmp_path, f"V{i}", "reel", posicao=i) for i in range(1, 11)]
          + [cand(tmp_path, f"E{i}", "image", posicao=100 + i) for i in range(1, 31)])
    legendas = {c.short_code: "hola" for c in cs}
    itens, resumo = planejar_es.montar_fila(
        cs, SLOTS, quantidade=40, legendas=legendas, nao_antes=agora(), upload=upload_falso
    )
    assert resumo["maior_sequencia_mesmo_tipo"] <= 4, "o feed sairia em blocos do mesmo tipo"


def test_alvo_de_video_pode_ser_forcado(tmp_path):
    cs = ([cand(tmp_path, f"V{i}", "reel", posicao=i) for i in range(1, 11)]
          + [cand(tmp_path, f"E{i}", "image", posicao=100 + i) for i in range(1, 11)])
    legendas = {c.short_code: "hola" for c in cs}
    _, resumo = planejar_es.montar_fila(
        cs, SLOTS, quantidade=10, legendas=legendas, alvo_video=0.5,
        nao_antes=agora(), upload=upload_falso
    )
    assert 0.4 <= resumo["proporcao_video"] <= 0.6


def test_origem_do_post_fica_registrada(tmp_path):
    cs = [cand(tmp_path, "C1", "image")]
    itens, _ = planejar_es.montar_fila(
        cs, SLOTS, quantidade=1, legendas={"C1": "hola"},
        nao_antes=agora(), upload=upload_falso,
    )
    origem = itens[0]["origem"]
    assert origem["formato"] == "Imagem única"
    assert origem["balde"] == "A"
    assert itens[0]["source_url"] == "https://www.instagram.com/p/C1/"


def test_item_e_serializavel_em_json(tmp_path):
    """A fila e gravada em disco; Path nao sobrevive a isso."""
    cs = [cand(tmp_path, "C1", "carousel", n_arquivos=3)]
    itens, _ = planejar_es.montar_fila(
        cs, SLOTS, quantidade=1, legendas={"C1": "hola"},
        nao_antes=agora(), upload=upload_falso,
    )
    json.dumps(itens)  # levanta TypeError se algum Path escapou


def test_item_carrega_o_horario_do_operador_alem_do_publico(tmp_path):
    """Ler o horario do Mexico com o relogio do Brasil ja fez parecer que um
    post tinha saido quando faltavam tres horas."""
    cs = [cand(tmp_path, "C1", "image")]
    itens, _ = planejar_es.montar_fila(
        cs, SLOTS, quantidade=1, legendas={"C1": "hola"},
        nao_antes=agora(), upload=upload_falso,
    )
    it = itens[0]
    publico = datetime.fromisoformat(it["scheduled_at"])
    operador = datetime.fromisoformat(it["scheduled_at_operador"])
    utc = datetime.fromisoformat(it["scheduled_at_utc"])
    # os tres apontam para o mesmo instante, em fusos diferentes
    assert publico.astimezone(UTC) == operador.astimezone(UTC)
    assert publico.astimezone(UTC) == utc.astimezone(UTC)
    # e o do operador esta 3h a frente (Mexico UTC-6, Brasil UTC-3)
    assert operador.hour == (publico.hour + 3) % 24
