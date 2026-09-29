"""Hospedar um lote precisa sobreviver a uma queda de rede.

Um lote de oito posts com carrossel sao dezenas de arquivos e varios minutos
de upload. Em 29/09/2026 a conexao com o GitHub caiu tres vezes no meio disso,
e o efeito era sempre o mesmo e sempre o pior: quase todos os arquivos ja
estavam hospedados, o comando abortava, e **nada** era gravado na fila. O
trabalho seguinte comecava do zero.

Tres mudancas, todas seguradas aqui:

* falha passageira de rede e repetida em vez de derrubar o lote;
* arquivo que ja esta na Release com o mesmo tamanho nao sobe de novo, o que
  torna a hospedagem retomavel;
* ``repo_slug`` em cache, porque era uma ida a api.github.com por arquivo.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from jayes_automation import hosting


@pytest.fixture(autouse=True)
def limpa_caches():
    # getattr: sem isto, tirar o @lru_cache de repo_slug quebra a fixture antes
    # do teste rodar, e a mutacao "falha" pelo motivo errado -- o teste precisa
    # reprovar pela contagem de chamadas, nao por AttributeError.
    limpar = getattr(hosting.repo_slug, "cache_clear", lambda: None)
    hosting._ASSETS_CACHE.clear()
    limpar()
    yield
    hosting._ASSETS_CACHE.clear()
    limpar()


def resultado(codigo=0, saida="", erro=""):
    return subprocess.CompletedProcess(args=["gh"], returncode=codigo, stdout=saida, stderr=erro)


class GhFalso:
    """Substitui o subprocess.run e anota o que foi chamado."""

    def __init__(self, respostas):
        self.respostas = respostas  # callable(args) -> CompletedProcess
        self.chamadas: list[list[str]] = []

    def __call__(self, args, **kwargs):
        self.chamadas.append(list(args))
        return self.respostas(list(args))

    def comandos(self, *inicio):
        alvo = list(inicio)
        return [c for c in self.chamadas if c[1 : 1 + len(alvo)] == alvo]


def instala(monkeypatch, respostas):
    gh = GhFalso(respostas)
    monkeypatch.setattr(hosting.subprocess, "run", gh)
    monkeypatch.setattr(hosting.time, "sleep", lambda _s: None)  # sem espera real
    monkeypatch.setenv("GITHUB_REPOSITORY", "dono/repo")
    return gh


# --- repeticao de falha passageira ----------------------------------------


def test_timeout_de_rede_e_repetido(monkeypatch):
    tentativas = {"n": 0}

    def respostas(args):
        if args[1:3] == ["release", "view"]:
            return resultado(0, json.dumps({"assets": []}))
        if args[1:3] == ["release", "upload"]:
            tentativas["n"] += 1
            if tentativas["n"] < 3:
                return resultado(1, erro="read: operation timed out")
            return resultado(0)
        return resultado(0)

    instala(monkeypatch, respostas)
    hosting._gh("release", "upload", "t", "/x")
    assert tentativas["n"] == 3, "devia ter repetido ate passar"


def test_erro_de_uso_nao_e_repetido(monkeypatch):
    """Repetir um erro real so gasta tempo e esconde a causa."""
    tentativas = {"n": 0}

    def respostas(args):
        tentativas["n"] += 1
        return resultado(1, erro="could not find release")

    instala(monkeypatch, respostas)
    with pytest.raises(hosting.HostingError):
        hosting._gh("release", "upload", "t", "/x")
    assert tentativas["n"] == 1


def test_desiste_depois_do_limite(monkeypatch):
    def respostas(args):
        return resultado(1, erro="read: operation timed out")

    gh = instala(monkeypatch, respostas)
    with pytest.raises(hosting.HostingError, match="timed out"):
        hosting._gh("release", "upload", "t", "/x")
    assert len(gh.chamadas) == hosting.TENTATIVAS


# --- hospedagem retomavel -------------------------------------------------


def test_arquivo_ja_hospedado_nao_sobe_de_novo(tmp_path, monkeypatch):
    arquivo = tmp_path / "video.mp4"
    arquivo.write_bytes(b"x" * 500)

    def respostas(args):
        if args[1:3] == ["release", "view"] and "--json" in args:
            return resultado(0, json.dumps({"assets": [{"name": "video.mp4", "size": 500}]}))
        return resultado(0)

    gh = instala(monkeypatch, respostas)
    info = hosting.upload_asset(arquivo, "media-v1")
    assert info["reaproveitado"] is True
    assert gh.comandos("release", "upload") == [], "nao devia ter subido nada"


def test_arquivo_com_tamanho_diferente_sobe(tmp_path, monkeypatch):
    """Midia reeditada tem outro tamanho: precisa substituir a antiga."""
    arquivo = tmp_path / "video.mp4"
    arquivo.write_bytes(b"x" * 900)

    def respostas(args):
        if args[1:3] == ["release", "view"] and "--json" in args:
            return resultado(0, json.dumps({"assets": [{"name": "video.mp4", "size": 500}]}))
        return resultado(0)

    gh = instala(monkeypatch, respostas)
    info = hosting.upload_asset(arquivo, "media-v1")
    assert "reaproveitado" not in info
    assert gh.comandos("release", "upload"), "devia ter subido a versao nova"


def test_lote_consulta_a_release_uma_vez_so(tmp_path, monkeypatch):
    """A lista de assets e uma chamada de rede; pedi-la por arquivo e desperdicio."""
    arquivos = []
    for n in range(5):
        p = tmp_path / f"slide-{n}.jpg"
        p.write_bytes(b"x" * 100)
        arquivos.append(p)

    def respostas(args):
        if args[1:3] == ["release", "view"] and "--json" in args:
            return resultado(0, json.dumps({"assets": []}))
        return resultado(0)

    gh = instala(monkeypatch, respostas)
    for p in arquivos:
        hosting.upload_asset(p, "media-v1")
    listagens = [c for c in gh.chamadas if c[1:3] == ["release", "view"] and "--json" in c]
    assert len(listagens) == 1, f"listou a release {len(listagens)} vezes"


def test_upload_atualiza_o_cache(tmp_path, monkeypatch):
    """Depois de subir, o proprio lote nao pode tentar subir o mesmo arquivo."""
    arquivo = tmp_path / "a.jpg"
    arquivo.write_bytes(b"x" * 100)

    def respostas(args):
        if args[1:3] == ["release", "view"] and "--json" in args:
            return resultado(0, json.dumps({"assets": []}))
        return resultado(0)

    gh = instala(monkeypatch, respostas)
    hosting.upload_asset(arquivo, "media-v1")
    hosting.upload_asset(arquivo, "media-v1")
    assert len(gh.comandos("release", "upload")) == 1


def test_colisao_de_nome_apaga_e_repete(tmp_path, monkeypatch):
    """O --clobber do gh perde a corrida consigo mesmo em lote grande."""
    arquivo = tmp_path / "a.jpg"
    arquivo.write_bytes(b"x" * 100)
    estado = {"primeira": True}

    def respostas(args):
        if args[1:3] == ["release", "view"] and "--json" in args:
            return resultado(0, json.dumps({"assets": []}))
        if args[1:3] == ["release", "upload"] and estado["primeira"]:
            estado["primeira"] = False
            return resultado(1, erro="HTTP 422: ReleaseAsset.name already exists")
        return resultado(0)

    gh = instala(monkeypatch, respostas)
    hosting.upload_asset(arquivo, "media-v1")
    assert gh.comandos("release", "delete-asset"), "devia ter apagado antes de repetir"
    assert len(gh.comandos("release", "upload")) == 2


# --- slug em cache --------------------------------------------------------


def test_slug_do_ambiente_nao_chama_a_rede(monkeypatch):
    gh = instala(monkeypatch, lambda args: resultado(0))
    assert hosting.repo_slug() == "dono/repo"
    assert gh.comandos("repo", "view") == []


def test_slug_do_git_e_consultado_uma_vez(monkeypatch):
    def respostas(args):
        return resultado(0, json.dumps({"nameWithOwner": "dono/repo"}))

    gh = instala(monkeypatch, respostas)
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
    for _ in range(4):
        assert hosting.repo_slug() == "dono/repo"
    assert len(gh.comandos("repo", "view")) == 1


def test_caminho_inexistente_falha_antes_de_qualquer_rede(tmp_path, monkeypatch):
    gh = instala(monkeypatch, lambda args: resultado(0))
    with pytest.raises(hosting.HostingError, match="nao existe"):
        hosting.upload_asset(Path(tmp_path / "sumiu.mp4"), "media-v1")
    assert gh.chamadas == []


# --- conta ativa ----------------------------------------------------------
#
# 'gh auth switch' e global e a maquina tem varias contas logadas. Tres vezes
# em dois dias a conta ativa trocou no meio de um lote, e a descoberta vinha no
# 404 do primeiro upload, com parte dos arquivos ja no ar.


def test_conta_errada_falha_antes_de_subir(monkeypatch):
    def respostas(args):
        if args[1:3] == ["api", "user"]:
            return resultado(0, "outra-pessoa\n")
        return resultado(0)

    gh = instala(monkeypatch, respostas)
    monkeypatch.setenv("GITHUB_REPOSITORY", "dono/repo")
    monkeypatch.delenv("GITHUB_REPOSITORY")
    monkeypatch.setattr(hosting, "repo_slug", lambda: "dono/repo")
    with pytest.raises(hosting.HostingError, match="gh auth switch --user dono"):
        hosting.conferir_conta()
    assert gh.comandos("release", "upload") == []


def test_conta_certa_passa(monkeypatch):
    def respostas(args):
        if args[1:3] == ["api", "user"]:
            return resultado(0, "dono\n")
        return resultado(0)

    instala(monkeypatch, respostas)
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
    monkeypatch.setattr(hosting, "repo_slug", lambda: "dono/repo")
    hosting.conferir_conta()


def test_no_runner_nao_confere_conta(monkeypatch):
    """No Actions a autenticacao vem do proprio runner; 'gh api user' so gastaria rede."""
    gh = instala(monkeypatch, lambda args: resultado(0, "seja-quem-for"))
    monkeypatch.setenv("GITHUB_REPOSITORY", "dono/repo")
    hosting.conferir_conta()
    assert gh.chamadas == []
