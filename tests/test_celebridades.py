"""Detector de figura publica.

O teste que mais importa e o de falso positivo: o perfil chama as proprias
alunas de "artistas" e menciona parceiros o tempo todo. Um detector que acusa
isso viraria ruido e seria ignorado -- que e como um detector morre.
"""

import pytest

from jayes_automation import celebridades as cel

# --- acusa o que deve -----------------------------------------------------


@pytest.mark.parametrize(
    "texto,pista",
    [
        ("Se sair um feat o Brasil desmonta! Torcendo! @anitta @badgalriri", "anitta"),
        ("Essa semana a cantora Adele encontra a cantora Céline Dion", "adele"),
        ("Está mais linda que nunca! Eu achei perfeita! E você? @anitta", "anitta"),
        ('"SE ARREPENDIMENTO MATASSE" - Luan Santana já dizia', "luan santana"),
        ("tatuar o nome do TR#MP na testa????", "trump"),
        ("É DO BRASILLLLLL! Fernanda Torres emociona o mundo", "fernanda torres"),
    ],
)
def test_acusa_figura_publica(texto, pista):
    assert cel.suspeito(texto), f"nao acusou {pista}"


def test_acusa_funcao_seguida_de_nome():
    s = cel.analisar("A atriz Mariana apareceu com as cejas assim")
    assert "funcao_e_nome" in s


def test_acusa_premiacao():
    assert cel.suspeito("O look do Oscar deste ano tinha uma sobrancelha impecável")


def test_tom_de_fofoca_so_conta_acompanhado():
    """'arrasou' sozinho descreve o proprio trabalho, nao uma celebridade."""
    assert not cel.suspeito("Minha cliente arrasou com esse resultado")
    s = cel.analisar("A Anitta arrasou no tapete vermelho")
    assert "tom_de_fofoca" in s


# --- nao acusa o que nao deve ---------------------------------------------


@pytest.mark.parametrize(
    "texto",
    [
        "Quero agradecer as artistas JAY.O que participaram do encontro",
        "Mais uma turma concluída com sucesso, ministrada por mim e por @fernandocrivelli",
        "São tantas etapas, desde que comecei: anos de estudo no curso de Biomedicina",
        "Trabalho de mais uma artista @jayo.pmu, a Rafaela Ferreira",
        "O scanner mede, mas não sente. Nunca vai traduzir o olhar da cliente",
        "Fonte: G1. O mercado de remoção de tatuagens é próspero",
    ],
)
def test_nao_acusa_conteudo_do_proprio_perfil(texto):
    assert not cel.suspeito(texto), f"falso positivo: {cel.descrever(cel.analisar(texto))}"


def test_texto_vazio():
    assert cel.analisar("") == {}
    assert not cel.suspeito("")


# --- descricao ------------------------------------------------------------


def test_descrever_e_legivel():
    d = cel.descrever(cel.analisar("A cantora Adele emocionou todo mundo"))
    assert "adele" in d.lower()
    assert d


def test_descrever_sem_sinais_e_vazio():
    assert cel.descrever({}) == ""


def test_mundial_do_proprio_setor_nao_e_celebridade():
    """O detector vetou isto por engano: 'mundial' sozinho e generico demais."""
    txt = (
        "Convite para treinar a Seleção Chinesa para participar do mundial de "
        "Micropigmentação em Roterdã na Holanda. Worldwide Eyebrow Festival."
    )
    assert not cel.suspeito(txt), cel.descrever(cel.analisar(txt))


def test_copa_do_mundo_continua_acusando():
    assert cel.suspeito("A sobrancelha que todo mundo comentou na copa do mundo")
