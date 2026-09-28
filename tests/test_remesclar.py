"""Alternar video e estatico na fila inteira, nao so dentro de um lote.

``montar_fila`` mescla o lote que esta montando. Isso basta quando o lote tem
os dois tipos e falha em silencio quando nao tem: em 28/09/2026 quatro Reels
legendados de uma vez entraram como quatro Reels seguidos, mesmo com a fila
equilibrada antes deles. O cliente pediu explicitamente mescla de tipos, entao
essa quebra e um defeito, nao um detalhe.

A regra que este arquivo segura: **o conjunto de horarios nao muda**. Nenhum
post e adiantado ou adiado -- so troca quem ocupa cada horario. Se isso
escorregar, um post pode ir ao ar antes da hora, e publicacao nao volta atras.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from jayes_automation import planejar_es

TZ = ZoneInfo("America/Mexico_City")
BASE = datetime(2026, 10, 1, 12, 0, tzinfo=TZ)


def item(ident, kind, *, horas, status="scheduled"):
    quando = BASE + timedelta(hours=horas)
    return {
        "id": f"q_{ident}",
        "tiktok_id": ident,
        "status": status,
        "media": {"kind": kind},
        "scheduled_at": quando.isoformat(),
        "scheduled_at_utc": quando.astimezone(ZoneInfo("UTC")).isoformat(),
        "scheduled_at_operador": quando.astimezone(ZoneInfo("America/Sao_Paulo")).isoformat(),
        "slot_id": "wd-midday",
    }


def fila(itens):
    return {"version": 2, "items": list(itens)}


def agora():
    # Tres horas antes do primeiro item: a janela intocavel e de uma hora, e
    # com 'agora = BASE - 1h' o item das 12h caia dentro dela e ficava de fora
    # da remescla. Um teste que nao percebe isso mede outra coisa.
    return BASE - timedelta(hours=3)


def tipos(f):
    return [
        i["media"]["kind"]
        for i in sorted(
            (x for x in f["items"] if x["status"] == "scheduled"),
            key=lambda x: x["scheduled_at"],
        )
    ]


def test_quebra_a_sequencia_de_quatro_iguais():
    """O caso real: 4 estaticos seguidos de 4 Reels."""
    f = fila(
        [item(f"E{i}", "image", horas=i * 7) for i in range(4)]
        + [item(f"V{i}", "reel", horas=28 + i * 7) for i in range(4)]
    )
    r = planejar_es.remesclar(f, agora=agora())
    assert r["sequencia_antes"] == 4
    assert r["sequencia_depois"] <= 2


def test_o_conjunto_de_horarios_nao_muda():
    """A garantia central: remesclar troca a ordem, nunca o relogio."""
    f = fila(
        [item(f"E{i}", "image", horas=i * 7) for i in range(3)]
        + [item(f"V{i}", "reel", horas=21 + i * 7) for i in range(3)]
    )
    antes = sorted(i["scheduled_at"] for i in f["items"])
    planejar_es.remesclar(f, agora=agora())
    assert sorted(i["scheduled_at"] for i in f["items"]) == antes


def test_os_tres_carimbos_de_horario_andam_juntos():
    """scheduled_at, _utc e _operador precisam apontar para o mesmo instante.

    Trocar so um deixaria a fila mentindo para quem a le -- e foi exatamente
    ler o horario errado que ja fez parecer que um post tinha saido.
    """
    f = fila([item("E1", "image", horas=0), item("V1", "reel", horas=7)])
    planejar_es.remesclar(f, agora=agora())
    for i in f["items"]:
        publico = datetime.fromisoformat(i["scheduled_at"])
        utc = datetime.fromisoformat(i["scheduled_at_utc"])
        operador = datetime.fromisoformat(i["scheduled_at_operador"])
        assert publico == utc == operador


def test_nao_toca_no_que_esta_prestes_a_sair():
    """Um item dentro de uma hora pode ja estar sendo publicado.

    O item iminente e um Reel cercado de estaticos de proposito: com essa
    proporcao o mesclador colocaria um estatico primeiro e empurraria o Reel
    para o fim. Se a janela intocavel sumir, o post que ia sair agora troca de
    horario com outro -- e publicacao nao volta atras. A primeira versao deste
    teste usava uma imagem iminente, que o mesclador deixaria em primeiro de
    qualquer jeito: passava com e sem a protecao, ou seja, nao testava nada.
    """
    f = fila(
        [
            item("JA", "reel", horas=0),
            item("E1", "image", horas=7),
            item("E2", "image", horas=14),
            item("E3", "image", horas=21),
        ]
    )
    # 'agora' 30 min antes do primeiro: ele entra na janela intocavel
    planejar_es.remesclar(f, agora=BASE - timedelta(minutes=30))
    primeiro = min(f["items"], key=lambda i: i["scheduled_at"])
    assert primeiro["tiktok_id"] == "JA"
    assert primeiro["scheduled_at"] == BASE.isoformat()


def test_publicado_nunca_e_remexido():
    f = fila(
        [
            item("PUB", "image", horas=-48, status="published"),
            item("V1", "reel", horas=7),
            item("V2", "reel", horas=14),
            item("E1", "image", horas=21),
        ]
    )
    antes = dict(
        (i["tiktok_id"], i["scheduled_at"]) for i in f["items"] if i["status"] == "published"
    )
    planejar_es.remesclar(f, agora=agora())
    depois = dict(
        (i["tiktok_id"], i["scheduled_at"]) for i in f["items"] if i["status"] == "published"
    )
    assert antes == depois


def test_preserva_a_ordem_dentro_do_tipo():
    """O melhor video continua saindo antes dos outros videos."""
    f = fila(
        [item(f"V{i}", "reel", horas=i * 7) for i in range(3)]
        + [item(f"E{i}", "image", horas=21 + i * 7) for i in range(3)]
    )
    planejar_es.remesclar(f, agora=agora())
    ordem = [i["tiktok_id"] for i in sorted(f["items"], key=lambda x: x["scheduled_at"])]
    videos = [x for x in ordem if x.startswith("V")]
    assert videos == ["V0", "V1", "V2"]


def test_fila_fica_em_ordem_cronologica_no_disco():
    """'publish-due' percorre a lista na ordem em que ela esta gravada."""
    f = fila(
        [item(f"E{i}", "image", horas=i * 7) for i in range(3)]
        + [item(f"V{i}", "reel", horas=21 + i * 7) for i in range(3)]
    )
    planejar_es.remesclar(f, agora=agora())
    horarios = [i["scheduled_at"] for i in f["items"]]
    assert horarios == sorted(horarios)


def test_um_item_so_nao_faz_nada():
    f = fila([item("V1", "reel", horas=7)])
    r = planejar_es.remesclar(f, agora=agora())
    assert r["remesclados"] == 0


def test_fila_de_um_tipo_so_nao_quebra():
    """Nao ha o que alternar; a funcao precisa sair inteira mesmo assim."""
    f = fila([item(f"V{i}", "reel", horas=i * 7) for i in range(4)])
    r = planejar_es.remesclar(f, agora=agora())
    assert r["sequencia_depois"] == 4
    assert tipos(f) == ["reel"] * 4
