"""Os workflows precisam chamar o comando que existe.

Este teste nasceu de uma falha em producao em 27/09/2026. A renomeacao do
pacote pulava diretorios cujo caminho contivesse ``/.git`` -- e ``.github``
contem ``.git`` como prefixo, entao a pasta inteira de workflows ficou de fora.
Tudo passava local, porque local se chama ``jayes`` a mao. No runner, os quatro
crons seguidos morreram com ``Failed to spawn: lukasmax`` e o post da noite
simplesmente nao saiu.

Um erro de nome num workflow so aparece quando o cron roda. Ate la, nenhum
teste, nenhum lint e nenhuma execucao local o encosta.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[1]
WORKFLOWS = sorted((RAIZ / ".github/workflows").glob("*.yml"))
SCRIPTS = sorted((RAIZ / "scripts").glob("*.sh"))


def comando_do_projeto() -> str:
    """O nome declarado em [project.scripts] do pyproject."""
    dados = tomllib.loads((RAIZ / "pyproject.toml").read_text(encoding="utf-8"))
    scripts = dados["project"]["scripts"]
    assert len(scripts) == 1, f"esperava um console script, achei {list(scripts)}"
    return next(iter(scripts))


def pacote_do_projeto() -> str:
    dados = tomllib.loads((RAIZ / "pyproject.toml").read_text(encoding="utf-8"))
    return next(iter(dados["project"]["scripts"].values())).split(".")[0]


def test_existe_workflow():
    assert WORKFLOWS, "nenhum workflow encontrado -- o glob mudou de lugar?"


@pytest.mark.parametrize("arquivo", WORKFLOWS, ids=lambda p: p.name)
def test_workflow_chama_o_comando_declarado_no_pyproject(arquivo: Path):
    texto = arquivo.read_text(encoding="utf-8")
    comando = comando_do_projeto()
    chamadas = re.findall(r"uv run ([a-zA-Z0-9_-]+)", texto)
    # 'uv run python' e 'uv run pytest' sao legitimos
    proprias = [c for c in chamadas if c not in {"python", "pytest", "ruff", "pip"}]
    for c in proprias:
        assert c == comando, (
            f"{arquivo.name} chama 'uv run {c}', mas o console script do projeto "
            f"e '{comando}'. No runner isso morre com 'Failed to spawn: {c}'."
        )


@pytest.mark.parametrize("arquivo", WORKFLOWS, ids=lambda p: p.name)
def test_workflow_importa_o_pacote_certo(arquivo: Path):
    texto = arquivo.read_text(encoding="utf-8")
    pacote = pacote_do_projeto()
    for achado in re.findall(r"from ([a-zA-Z0-9_]+) import", texto):
        if achado.endswith("_automation"):
            assert achado == pacote, f"{arquivo.name} importa '{achado}', esperado '{pacote}'"


@pytest.mark.parametrize("arquivo", SCRIPTS, ids=lambda p: p.name)
def test_script_chama_o_comando_declarado(arquivo: Path):
    texto = arquivo.read_text(encoding="utf-8")
    comando = comando_do_projeto()
    for c in re.findall(r"uv run ([a-zA-Z0-9_-]+)", texto):
        if c in {"python", "pytest", "ruff", "pip"}:
            continue
        assert c == comando, f"{arquivo.name} chama 'uv run {c}', esperado '{comando}'"


def test_nenhum_workflow_carrega_nome_do_projeto_irmao():
    """Guarda ampla: qualquer sobra de 'lukasmax' em workflow e sintoma.

    Sem caixa: a primeira versao comparava minusculas e por isso nao via o
    secret ``LUKASMAX_CANARY`` no token.yml, que ficou la depois da renomeacao.
    Nome de secret e de variavel de ambiente vive em maiusculas -- procurar so
    por 'lukasmax' cobria justamente a metade errada do arquivo.
    """
    sobras = []
    for arquivo in WORKFLOWS + SCRIPTS:
        for n, linha in enumerate(arquivo.read_text(encoding="utf-8").splitlines(), 1):
            if "lukasmax" in linha.lower():
                sobras.append(f"{arquivo.name}:{n}: {linha.strip()}")
    assert not sobras, "sobrou nome do projeto irmao:\n" + "\n".join(sobras)
