"""Midia reeditada para tirar o portugues queimado na imagem.

O risco aqui e especifico: a edicao acontece na minha maquina, num arquivo que
o git ignora, e o que vai ao ar e o que foi hospedado. Se a substituicao falhar
em silencio, o post sai com o texto em portugues apesar de a edicao ter sido
feita e conferida -- e ninguem descobre ate ver o post publicado.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from jayes_automation import adaptar_es, ingest

RAIZ = Path(__file__).resolve().parents[1]
RECEITAS_REAIS = RAIZ / "data" / "adaptacoes-es.json"

RECEITA = {
    "arquivo": "video.mp4",
    "motivo": "card final em portugues",
    "filtros": "delogo=x=1:y=2:w=3:h=4",
}


# --- o comando -------------------------------------------------------------


def test_comando_carrega_o_filtro_da_receita(tmp_path):
    cmd = adaptar_es.comando(tmp_path / "a.mp4", tmp_path / "b.mp4", RECEITA)
    assert cmd[cmd.index("-vf") + 1] == "delogo=x=1:y=2:w=3:h=4"


def test_comando_limpa_o_metadado_de_origem(tmp_path):
    """O arquivo vai para uma Release publica; nao precisa dizer de onde veio."""
    cmd = adaptar_es.comando(tmp_path / "a.mp4", tmp_path / "b.mp4", RECEITA)
    assert cmd[cmd.index("-map_metadata") + 1] == "-1"


def test_comando_corta_quando_a_receita_pede(tmp_path):
    receita = {**RECEITA, "ate_segundos": 50}
    cmd = adaptar_es.comando(tmp_path / "a.mp4", tmp_path / "b.mp4", receita)
    assert cmd[cmd.index("-t") + 1] == "50"


def test_comando_sem_corte_nao_passa_menos_t(tmp_path):
    assert "-t" not in adaptar_es.comando(tmp_path / "a.mp4", tmp_path / "b.mp4", RECEITA)


def test_receita_sem_filtro_e_erro(tmp_path):
    with pytest.raises(adaptar_es.AdaptacaoError, match="sem 'filtros'"):
        adaptar_es.comando(tmp_path / "a.mp4", tmp_path / "b.mp4", {"arquivo": "v.mp4"})


def test_origem_inexistente_e_erro(tmp_path):
    with pytest.raises(adaptar_es.AdaptacaoError, match="origem nao existe"):
        adaptar_es.aplicar("X", tmp_path / "sumiu.mp4", RECEITA, tmp_path / "out")


# --- a substituicao --------------------------------------------------------


def test_substituir_troca_so_o_que_foi_adaptado(tmp_path):
    originais = []
    for nome in ("slide-01.jpg", "slide-02.jpg"):
        p = tmp_path / "orig" / nome
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x")
        originais.append(p)

    adaptado = tmp_path / "adaptado" / "ABC" / "slide-02.jpg"
    adaptado.parent.mkdir(parents=True)
    adaptado.write_bytes(b"y")

    saida = adaptar_es.substituir(originais, "ABC", tmp_path / "adaptado")
    assert saida[0] == originais[0], "slide sem versao adaptada deveria passar intacto"
    assert saida[1] == adaptado


def test_substituir_sem_pasta_devolve_os_originais(tmp_path):
    originais = [tmp_path / "video.mp4"]
    assert adaptar_es.substituir(originais, "ABC", tmp_path / "nao-existe") == originais


def test_ingest_usa_a_midia_adaptada(tmp_path):
    """A ponta que importa: e o arquivo adaptado que sobe para a Release."""
    midia = tmp_path / "melhores-conteudos"
    pasta = midia / "001_Reel_2026-01-01_x"
    pasta.mkdir(parents=True)
    (pasta / "video.mp4").write_bytes(b"original")
    dados = tmp_path / "data"
    dados.mkdir()
    (dados / "triagem-es.json").write_text(
        json.dumps(
            [
                {
                    "url": "https://www.instagram.com/p/ABC/",
                    "pasta": pasta.name,
                    "formato": "Reel",
                    "balde": "A",
                    "posicao": 1,
                    "score": 1.0,
                }
            ]
        ),
        encoding="utf-8",
    )
    (dados / "manifesto-x.json").write_text(json.dumps({"itens": []}), encoding="utf-8")

    adaptado = tmp_path / "adaptado" / "ABC" / "video.mp4"
    adaptado.parent.mkdir(parents=True)
    adaptado.write_bytes(b"adaptado-em-espanhol")

    sem = ingest.carregar(midia, dados)
    assert sem[0].arquivos[0].read_bytes() == b"original"

    com = ingest.carregar(midia, dados, dir_adaptado=tmp_path / "adaptado")
    assert com[0].arquivos[0].read_bytes() == b"adaptado-em-espanhol"


# --- as receitas de verdade ------------------------------------------------


def test_arquivo_de_receitas_e_valido():
    receitas = adaptar_es.carregar(RECEITAS_REAIS)
    assert receitas, "nenhuma receita registrada"
    for short_code, receita in receitas.items():
        # 'motivo' e 'arquivo' valem para qualquer receita; 'filtros' so para as
        # de video, porque os cards tipograficos sao refeitos com PIL e nao
        # passam pelo ffmpeg.
        assert receita.get("motivo"), f"{short_code} sem motivo registrado"
        assert receita.get("arquivo"), f"{short_code} sem arquivo alvo"
        if receita.get("tipo") != "imagem_texto":
            assert receita.get("filtros"), f"{short_code} sem filtros"


def test_toda_receita_produz_um_comando_montavel(tmp_path):
    """Um filtro escrito errado so apareceria depois de minutos de encode."""
    for short_code, receita in adaptar_es.carregar(RECEITAS_REAIS).items():
        if receita.get("tipo") == "imagem_texto":
            continue
        cmd = adaptar_es.comando(tmp_path / "a.mp4", tmp_path / "b.mp4", receita)
        assert cmd[0] == "ffmpeg", short_code
        assert str(tmp_path / "b.mp4") == cmd[-1], short_code


# --- cards tipograficos ---------------------------------------------------
#
# Alguns posts sao a frase: fundo chapado, texto em portugues e nada mais.
# Publicar sem traduzir seria publicar em portugues, entao aqui a imagem e
# refeita em vez de remendada.


def receita_card(linhas):
    return {
        "tipo": "imagem_texto",
        "arquivo": "imagem.jpg",
        "motivo": "card tipografico em portugues",
        "apagar": [10, 10, 190, 90],
        "cor_fundo_em": [2, 2],
        "fonte": "/System/Library/Fonts/Avenir Next.ttc",
        "fonte_indice": 8,
        "tamanho": 20,
        "texto_em": [15, 15],
        "altura_linha": 24,
        "linhas": linhas,
    }


def card(tmp_path, cor=(255, 255, 255)):
    Image = pytest.importorskip("PIL.Image")
    caminho = tmp_path / "imagem.jpg"
    Image.new("RGB", (200, 100), cor).save(caminho)
    return caminho


def test_redesenhar_gera_o_arquivo(tmp_path):
    origem = card(tmp_path)
    destino = tmp_path / "out" / "imagem.jpg"
    adaptar_es.redesenhar_texto(origem, destino, receita_card(["Hola", "mundo"]))
    assert destino.exists()


def test_redesenhar_preserva_as_dimensoes(tmp_path):
    """A Meta corta o carrossel pela proporcao do primeiro slide: mudar o
    tamanho de um slide editado desalinharia o post inteiro."""
    Image = pytest.importorskip("PIL.Image")
    origem = card(tmp_path)
    destino = tmp_path / "out" / "imagem.jpg"
    adaptar_es.redesenhar_texto(origem, destino, receita_card(["Hola"]))
    assert Image.open(destino).size == Image.open(origem).size


def test_redesenhar_apaga_o_texto_antigo(tmp_path):
    """O retangulo de limpeza tem que cobrir mesmo: sobra de letra em
    portugues por baixo do texto novo e pior que nao editar."""
    Image = pytest.importorskip("PIL.Image")
    ImageDraw = pytest.importorskip("PIL.ImageDraw")
    origem = tmp_path / "imagem.jpg"
    im = Image.new("RGB", (200, 100), (255, 255, 255))
    ImageDraw.Draw(im).rectangle([20, 20, 180, 80], fill=(0, 0, 0))  # "texto" antigo
    im.save(origem)

    destino = tmp_path / "out" / "imagem.jpg"
    adaptar_es.redesenhar_texto(origem, destino, receita_card([""]))
    px = Image.open(destino).convert("L").load()
    assert px[100, 50] > 200, "a area antiga continua escura: o retangulo nao cobriu"


def test_aplicar_despacha_o_card_sem_chamar_ffmpeg(tmp_path, monkeypatch):
    """Um card e imagem; invocar ffmpeg aqui so encontraria um erro obscuro."""
    monkeypatch.setattr(adaptar_es.subprocess, "run", lambda *a, **k: pytest.fail("chamou ffmpeg"))
    origem = card(tmp_path)
    destino = adaptar_es.aplicar("X", origem, receita_card(["Hola"]), tmp_path / "ad")
    assert destino.exists()


def test_receitas_de_card_no_repositorio_tem_o_que_precisam():
    for short_code, r in adaptar_es.carregar(RECEITAS_REAIS).items():
        if r.get("tipo") != "imagem_texto":
            continue
        for chave in ("apagar", "fonte", "tamanho", "texto_em", "altura_linha", "linhas"):
            assert r.get(chave), f"{short_code} sem '{chave}'"
        assert len(r["apagar"]) == 4, f"{short_code}: 'apagar' precisa de 4 coordenadas"
