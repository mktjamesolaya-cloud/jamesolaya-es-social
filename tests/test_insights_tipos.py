"""Coleta de metricas por tipo de midia, e a degradacao quando a Meta recusa.

O buraco que estes testes fecham: o coletor pedia sempre o conjunto de Reel.
Com 70% da fila em foto e carrossel, o ajuste de horario aprenderia so com os
Reels -- e sem erro visivel, porque a falha e por post.
"""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from jayes_automation import insights as ins

TZ = ZoneInfo("America/Mexico_City")


class LeitorFalso:
    """Recusa metricas que nao valem para o tipo, como a Meta faz."""

    def __init__(self, recusa=()):
        self.recusa = set(recusa)
        self.pedidos: list[str] = []

    def media_insights(self, media_id, metrics):
        self.pedidos.append(metrics)
        pedidas = set(metrics.split(","))
        if pedidas & self.recusa:
            raise RuntimeError("(#100) metrica invalida para este tipo de midia")
        return {"data": [{"name": m, "values": [{"value": 10}]} for m in pedidas]}


def item(kind, id_="q1"):
    publicado = datetime.now(TZ) - timedelta(hours=25)
    return {
        "id": id_,
        "status": "published",
        "instagram_media_id": "m1",
        "tiktok_id": "ABC",
        "slot_id": "evening",
        "scheduled_at": publicado.isoformat(),
        "published_at": publicado.isoformat(),
        "media": {"kind": kind},
    }


# --- conjunto por tipo ----------------------------------------------------


def test_reel_pede_a_metrica_de_reel():
    assert "ig_reels_avg_watch_time" in ins.metricas_de(item("reel"))


@pytest.mark.parametrize("kind", ["image", "carousel"])
def test_estatico_nao_pede_metrica_de_reel(kind):
    assert "ig_reels_avg_watch_time" not in ins.metricas_de(item(kind))


def test_estatico_ainda_pede_views_que_e_o_que_decide_horario():
    assert "views" in ins.metricas_de(item("image"))


def test_item_antigo_sem_kind_e_tratado_como_reel():
    assert "ig_reels_avg_watch_time" in ins.metricas_de({"media": {}})


# --- coleta ---------------------------------------------------------------


def _coleta(tmp_path, it, leitor):
    return ins.collect({"items": [it]}, leitor, tmp_path / "insights.csv")


def test_coleta_de_imagem_funciona(tmp_path):
    leitor = LeitorFalso(recusa={"ig_reels_avg_watch_time"})
    r = _coleta(tmp_path, item("image"), leitor)
    assert r["coletados"] == 1, r
    assert r["erros"] == 0


def test_coleta_de_reel_pede_a_metrica_de_reel(tmp_path):
    leitor = LeitorFalso()
    r = _coleta(tmp_path, item("reel"), leitor)
    assert r["coletados"] == 1
    assert "ig_reels_avg_watch_time" in leitor.pedidos[0]


def test_degrada_para_o_nucleo_quando_a_meta_recusa(tmp_path):
    """Se a Meta passar a recusar uma metrica, o post nao pode ser perdido."""
    leitor = LeitorFalso(recusa={"ig_reels_avg_watch_time"})
    r = _coleta(tmp_path, item("reel"), leitor)
    assert r["coletados"] == 1, "perdeu o post em vez de tentar o nucleo"
    assert len(leitor.pedidos) == 2, "nao tentou o conjunto reduzido"
    assert "ig_reels_avg_watch_time" not in leitor.pedidos[1]


def test_marca_quando_a_coleta_foi_reduzida(tmp_path):
    leitor = LeitorFalso(recusa={"ig_reels_avg_watch_time"})
    _coleta(tmp_path, item("reel"), leitor)
    assert "metricas_reduzidas" in (tmp_path / "insights.csv").read_text()


def test_desiste_so_quando_ate_o_nucleo_falha(tmp_path):
    leitor = LeitorFalso(recusa={"views"})
    r = _coleta(tmp_path, item("image"), leitor)
    assert r["erros"] == 1
    assert r["coletados"] == 0


def test_linha_registra_o_tipo_para_a_analise_separar(tmp_path):
    _coleta(tmp_path, item("carousel"), LeitorFalso(recusa={"ig_reels_avg_watch_time"}))
    assert "carousel" in (tmp_path / "insights.csv").read_text()
