"""Regras de legenda em espanhol.

O teste que mais importa e o do portugues vazando: e o modo de falha proprio
deste projeto, e o unico cujo custo e publicar errado para o publico errado.
"""

import pytest

from jayes_automation import captions_es as ces

BOA = (
    "Ella llegó escondiendo la frente con flequillo desde hacía ocho años. "
    "No quería una ceja nueva: quería reconocerse en el espejo. "
    "Reconstruimos pelo por pelo, respetando el trazo que la naturaleza le había dado."
)
TAGS = ["#micropigmentacion", "#cejas", "#alopecia", "#belleza"]


def test_legenda_boa_passa_sem_aviso():
    assert ces.validate_es(BOA, TAGS) == []


# --- portugues vazando ----------------------------------------------------

@pytest.mark.parametrize("trecho,pista", [
    ("Ela não queria uma ceja nueva, queria se reconhecer no espelho de casa hoy", "não"),
    ("Reconstruímos pelo por pelo con mucha atenção al trazo natural de la clienta", "ção"),
    ("Você va a ver el resultado en la próxima foto de esta secuencia completa", "você"),
    ("El resultado es uma transformação real para la clienta que llegó insegura", "cedilha"),
])
def test_acusa_portugues(trecho, pista):
    avisos = ces.validate_es(trecho + " " * 20, TAGS)
    assert any("portugues" in a for a in avisos), f"nao acusou {pista}: {avisos}"


def test_nao_acusa_palavra_igual_nos_dois_idiomas():
    """'natural', 'técnica' e 'profesional' existem nos dois. Falso positivo seria constante."""
    txt = ("Un resultado natural depende de la técnica y del ojo del profesional, "
           "no del color del pigmento ni del equipo que uses en la sesión.")
    assert ces.detectar_portugues(txt) == []
    assert not any("portugues" in a for a in ces.validate_es(txt, TAGS))


def test_acento_solto_em_texto_claramente_espanhol_nao_bloqueia():
    """Um typo num texto cheio de marcas de espanhol e typo, nao portugues."""
    txt = ("¿Cómo saber si tu shadow está bien hecho? El borde no puede verse. "
           "Cuando se ve dónde empieza, la piel ya perdió naturalidad y el resultado és plano.")
    avisos = ces.validate_es(txt, TAGS)
    assert not any("portugues" in a for a in avisos), avisos


def test_hashtag_em_portugues_e_acusada():
    avisos = ces.validate_es(BOA, ["#micropigmentação", "#cejas", "#alopecia"])
    assert any("hashtag em portugues" in a for a in avisos), avisos


def test_detectar_portugues_devolve_lista_vazia_para_espanhol():
    assert ces.detectar_portugues(BOA) == []


# --- tamanho e gancho -----------------------------------------------------

def test_legenda_longa_e_aceita():
    """A regra herdada do perfil irmao exigia <=125 e reprovaria este perfil inteiro."""
    txt = "Cada sesión empieza con una conversación, no con la aguja. " * 12
    avisos = ces.validate_es(txt.strip(), TAGS)
    assert not any("caracteres" in a for a in avisos), avisos


def test_acima_do_teto_do_instagram_e_barrado():
    avisos = ces.validate_es("a" * 2300, TAGS)
    assert any("limite do Instagram" in a for a in avisos)


def test_legenda_curta_demais_e_barrada():
    """Curta aqui quase sempre significa adaptacao pela metade."""
    avisos = ces.validate_es("Qué belleza de resultado.", TAGS)
    assert any("curta demais" in a for a in avisos), avisos


def test_gancho_que_corta_no_meio_e_acusado():
    """O corte do Instagram cai em 'la', deixando o gancho pendurado."""
    txt = ("Un resultado natural depende de la técnica y del ojo del profesional, "
           "no del color del pigmento ni del equipo que uses en la sesión.")
    assert txt[:ces.HOOK_CHARS].rstrip().endswith(" la"), "premissa do teste mudou"
    assert any("gancho corta" in a for a in ces.validate_es(txt, TAGS))


def test_gancho_que_fecha_bem_nao_e_acusado():
    txt = ("Ella llegó escondiendo la frente con flequillo desde hacía ocho años y hoy "
           "vuelve a mirarse al espejo sin buscar el ángulo que la disimulaba antes.")
    assert not any("gancho corta" in a for a in ces.validate_es(txt, TAGS))


def test_espaco_nas_pontas_e_acusado():
    assert any("espaco em branco" in a for a in ces.validate_es("  " + BOA + "  ", TAGS))


def test_legenda_vazia():
    assert ces.validate_es("", TAGS) == ["legenda vazia"]


# --- hashtags -------------------------------------------------------------

def test_poucas_hashtags():
    assert any("alvo: 3 a 8" in a for a in ces.validate_es(BOA, ["#cejas"]))


def test_muitas_hashtags():
    assert any("alvo: 3 a 8" in a for a in ces.validate_es(BOA, [f"#t{i}" for i in range(12)]))


def test_hashtag_sem_cerquilha():
    assert any("sem '#'" in a for a in ces.validate_es(BOA, ["cejas", "#a", "#b"]))


def test_hashtag_com_espaco():
    assert any("com espaco" in a for a in ces.validate_es(BOA, ["#dos palabras", "#a", "#b"]))


# --- registro -------------------------------------------------------------

def test_montar_gera_registro_com_avisos():
    r = ces.montar("ABC", {"x": 1}, caption=BOA, hashtags=TAGS)
    assert r["tiktok_id"] == "ABC"
    assert r["warnings"] == []
    assert r["status"] == "draft"
    assert r["prompt_version"] == "es-v1"
    assert r["input_fingerprint"]


def test_montar_carrega_os_avisos_de_uma_legenda_ruim():
    r = ces.montar("ABC", {"x": 1}, caption="Você não vai acreditar nesse resultado lindo", hashtags=["#a"])
    assert r["warnings"], "devia ter acusado portugues e hashtags de menos"


def test_texto_completo_junta_legenda_e_hashtags():
    r = ces.montar("ABC", {"x": 1}, caption=BOA, hashtags=TAGS)
    completo = ces.texto_completo(r)
    assert completo.startswith("Ella llegó")
    assert completo.endswith("#belleza")
    assert "\n\n" in completo


def test_texto_completo_sem_hashtags():
    r = ces.montar("ABC", {"x": 1}, caption=BOA, hashtags=[])
    assert ces.texto_completo(r) == BOA
