"""O doctor precisa ver o token vencendo antes de ele vencer.

Ate 28/09/2026 ele conferia fila, assets, conta e quota -- e nunca prazo. O
token do Painel de Apps vale 60 dias e, passado o prazo, nao ha renovacao: so
gerar outro a mao. Ou seja, o doctor dizia "ok: true" a um dia do fim, e a
publicacao pararia sem um unico aviso, nem aqui nem no app.

A Graph API do Instagram nao tem 'debug_token', entao a validade nao da para
perguntar: ela e anotada em ``data/token-estado.json`` a cada renovacao. Isso
traz um segundo sinal de graca -- se a anotacao envelhece, o cron mensal parou,
que e o sintoma de faltar o secret SECRETS_PAT.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from jayes_automation import cli


def monta_projeto(tmp_path, *, expira_em_dias: int | None, renovado_ha_dias: int = 1):
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    (tmp_path / "data" / "queue.json").write_text(
        json.dumps({"version": 2, "items": []}), encoding="utf-8"
    )
    if expira_em_dias is not None:
        agora = datetime.now(UTC)
        (tmp_path / "data" / "token-estado.json").write_text(
            json.dumps(
                {
                    "renovado_em": (agora - timedelta(days=renovado_ha_dias)).isoformat(),
                    "expira_em": (agora + timedelta(days=expira_em_dias)).isoformat(),
                    "validade_dias": 60,
                }
            ),
            encoding="utf-8",
        )
    return tmp_path


def roda_doctor(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("PUBLISH_ENABLED", raising=False)
    codigo = cli.main(["--root", str(tmp_path), "doctor"])
    return codigo, json.loads(capsys.readouterr().out)


def test_token_com_folga_passa(tmp_path, monkeypatch, capsys):
    monta_projeto(tmp_path, expira_em_dias=57)
    codigo, saida = roda_doctor(tmp_path, monkeypatch, capsys)
    assert codigo == 0
    assert saida["ok"] is True
    assert saida["notas"]["token_expira_em_dias"] == 56


@pytest.mark.parametrize("dias", [20, 7, 1, 0])
def test_token_perto_do_fim_reprova(tmp_path, monkeypatch, capsys, dias):
    monta_projeto(tmp_path, expira_em_dias=dias)
    codigo, saida = roda_doctor(tmp_path, monkeypatch, capsys)
    assert codigo == 1
    assert saida["ok"] is False
    assert any("vence em" in p for p in saida["problemas"]), saida["problemas"]


def test_sem_o_arquivo_de_estado_o_doctor_reclama(tmp_path, monkeypatch, capsys):
    """ "Nao sei quando vence" nao pode passar por "esta tudo bem"."""
    monta_projeto(tmp_path, expira_em_dias=None)
    codigo, saida = roda_doctor(tmp_path, monkeypatch, capsys)
    assert codigo == 1
    assert any("token-estado.json" in p for p in saida["problemas"]), saida["problemas"]


def test_renovacao_velha_denuncia_o_cron_parado(tmp_path, monkeypatch, capsys):
    """O cron roda todo dia 1. Sessenta dias sem renovar = ele nao esta rodando.

    E o jeito de o SECRETS_PAT faltando aparecer aqui em vez de so no dia em
    que o token vencer.
    """
    monta_projeto(tmp_path, expira_em_dias=40, renovado_ha_dias=60)
    codigo, saida = roda_doctor(tmp_path, monkeypatch, capsys)
    assert codigo == 1
    assert any("SECRETS_PAT" in p for p in saida["problemas"]), saida["problemas"]


def test_o_estado_real_do_repositorio_esta_coerente():
    """O arquivo versionado precisa bater com o que o doctor sabe ler."""
    from pathlib import Path

    dados = json.loads(
        (Path(__file__).resolve().parents[1] / "data" / "token-estado.json").read_text(
            encoding="utf-8"
        )
    )
    renovado = datetime.fromisoformat(dados["renovado_em"])
    expira = datetime.fromisoformat(dados["expira_em"])
    assert expira > renovado
    assert (expira - renovado).days == dados["validade_dias"]
