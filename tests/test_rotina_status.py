"""A decisao de "trabalhar agora?" da rotina horaria de legendas.

A rotina roda de hora em hora e, quase sempre, a resposta certa e nao fazer
nada -- a fila consome 2 posts por dia e um lote cobre varios. Este comando e
o portao: se ele disser 'trabalhar: true' quando nao devia, o robo escreve
legendas que ninguem pediu e enche a fila alem do necessario; se disser false
quando devia trabalhar, a fila seca em silencio, que foi exatamente o que
aconteceu em 28/09/2026.

Ver docs/ROTINA_LEGENDAS.md.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from jayes_automation import cli

TZ = ZoneInfo("America/Mexico_City")

SLOTS = {
    "version": 1,
    "timezone": "America/Mexico_City",
    "posts_per_day": 2,
    "min_gap_minutes": 300,
    "jitter_minutes": 0,
    "pool": [
        {"id": "wd-midday", "weekdays": [0, 1, 2, 3, 4], "time": "12:00", "weight": 1.0},
        {"id": "wd-evening", "weekdays": [0, 1, 2, 3, 4], "time": "19:00", "weight": 1.0},
    ],
}


def projeto(tmp_path, itens, *, candidatos=20):
    dados = tmp_path / "data"
    dados.mkdir(parents=True, exist_ok=True)
    (dados / "queue.json").write_text(
        json.dumps({"version": 2, "items": itens}), encoding="utf-8"
    )
    (dados / "slots.json").write_text(json.dumps(SLOTS), encoding="utf-8")

    # Acervo de mentira, numa raiz propria: a midia e a triagem vivem no projeto
    # de analise, nao junto da fila. Ler os dois do mesmo lugar ja foi um bug
    # aqui (o veto era ignorado em silencio), entao o teste tambem os separa.
    acervo = tmp_path / "acervo"
    midia = acervo / "melhores-conteudos"
    triagem = []
    for n in range(candidatos):
        pasta = midia / f"{n:03d}_Imagem-unica_2026-01-01_x{n}"
        pasta.mkdir(parents=True)
        (pasta / "imagem.jpg").write_bytes(b"x")
        triagem.append(
            {
                "url": f"https://www.instagram.com/p/POST{n:03d}/",
                "pasta": pasta.name,
                "formato": "Imagem única",
                "balde": "A",
                "posicao": n + 1,
                "score": 10.0 - n,
            }
        )
    (acervo / "data").mkdir(parents=True)
    (acervo / "data" / "triagem-es.json").write_text(
        json.dumps(triagem), encoding="utf-8"
    )
    (acervo / "data" / "manifesto-x.json").write_text(
        json.dumps({"itens": []}), encoding="utf-8"
    )
    return tmp_path, acervo


def item(*, daqui_a_dias: float, status: str = "scheduled", ident: str = "q1"):
    quando = datetime.now(TZ) + timedelta(days=daqui_a_dias)
    return {
        "id": ident,
        "tiktok_id": ident.upper(),
        "status": status,
        "scheduled_at": quando.isoformat(),
    }


def roda(tmp_path, acervo, capsys, *extra):
    codigo = cli.main(
        ["--root", str(tmp_path), "rotina-status", "--acervo", str(acervo), *extra]
    )
    return codigo, json.loads(capsys.readouterr().out)


def test_fila_com_folga_nao_trabalha(tmp_path, capsys):
    tmp_path, acervo = projeto(tmp_path, [item(daqui_a_dias=8)])
    _, saida = roda(tmp_path, acervo, capsys)
    assert saida["trabalhar"] is False
    assert any("cobre" in m for m in saida["motivos_para_nao"])


def test_item_preso_em_publishing_trava_tudo(tmp_path, capsys):
    """Acrescentar a fila enquanto uma publicacao esta a meio caminho e pedir
    para duplicar um post -- a unica falha irreversivel deste projeto."""
    tmp_path, acervo = projeto(
        tmp_path, [item(daqui_a_dias=1, status="publishing", ident="q1")]
    )
    _, saida = roda(tmp_path, acervo, capsys)
    assert saida["trabalhar"] is False
    assert any("publishing" in m for m in saida["motivos_para_nao"])


def test_a_folga_e_configuravel(tmp_path, capsys):
    tmp_path, acervo = projeto(tmp_path, [item(daqui_a_dias=4)])
    # alvo de 10 dias: uma fila de 4 e curta, entao ha trabalho a fazer
    _, alta = roda(tmp_path, acervo, capsys, "--folga-dias", "10")
    assert alta["trabalhar"] is True
    # alvo de 2 dias: a mesma fila ja basta
    _, baixa = roda(tmp_path, acervo, capsys, "--folga-dias", "2")
    assert baixa["trabalhar"] is False
    assert any("cobre" in m for m in baixa["motivos_para_nao"])


def test_conta_os_dias_a_partir_do_ultimo_agendado(tmp_path, capsys):
    tmp_path, acervo = projeto(
        tmp_path,
        [
            item(daqui_a_dias=1, ident="q1"),
            item(daqui_a_dias=7, ident="q2"),
            item(daqui_a_dias=3, ident="q3"),
        ],
    )
    _, saida = roda(tmp_path, acervo, capsys)
    assert 6.9 <= saida["folga_dias"] <= 7.1
    assert saida["itens_agendados"] == 3


def test_publicado_nao_conta_como_fila(tmp_path, capsys):
    """Um post que ja saiu nao adianta folga nenhuma."""
    tmp_path, acervo = projeto(
        tmp_path,
        [
            {**item(daqui_a_dias=9, ident="q1"), "status": "published"},
            item(daqui_a_dias=1, ident="q2"),
        ],
    )
    _, saida = roda(tmp_path, acervo, capsys)
    assert saida["itens_agendados"] == 1
    assert saida["folga_dias"] < 2


def test_fila_vazia_pede_trabalho(tmp_path, capsys):
    """O caso de 28/09: quatro itens publicados, nada agendado, conta parada."""
    tmp_path, acervo = projeto(
        tmp_path, [{**item(daqui_a_dias=-1, ident="q1"), "status": "published"}]
    )
    _, saida = roda(tmp_path, acervo, capsys)
    assert saida["folga_dias"] == 0.0
    assert not any("cobre" in m for m in saida["motivos_para_nao"])


def test_diz_quando_nao_conseguiu_ler_o_git(tmp_path, capsys):
    """"Nao sei" nao pode se confundir com "esta limpo"."""
    tmp_path, acervo = projeto(tmp_path, [item(daqui_a_dias=8)])
    _, saida = roda(tmp_path, acervo, capsys)
    assert saida["git_legivel"] is False, "tmp_path nao e um repositorio git"


@pytest.mark.parametrize("dias", [0.0, 2.0, 5.9])
def test_fila_curta_libera_o_trabalho(tmp_path, capsys, dias):
    tmp_path, acervo = projeto(tmp_path, [item(daqui_a_dias=dias)])
    _, saida = roda(tmp_path, acervo, capsys)
    assert not any("cobre" in m for m in saida["motivos_para_nao"]), saida
