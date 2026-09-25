"""Regras de legenda para o @jamesolaya.es.

Por que nao reusar ``captions.py`` inteiro: a v2 de la exige **uma frase de ate
125 caracteres**. Foi uma escolha deliberada para um perfil de humor musical, e
nao transfere. As legendas do @jamesolaya tem mediana de 816 caracteres e 76 de
80 passam de 125 -- aplicar a regra herdada reprovaria quase tudo.

O que transfere e a parte que e verdade do Instagram, nao do perfil:

* os primeiros ~125 caracteres sao tudo que aparece antes do "mais", entao e ali
  que o gancho tem de viver e ali que ele nao pode cortar no meio de uma frase;
* o teto duro sao 2.200 caracteres;
* hashtag precisa de ``#`` e nao pode ter espaco.

E entra uma regra que so existe aqui: **portugues vazando**. E o modo de falha
proprio deste projeto -- a legenda nasce em portugues e e reescrita, entao um
"ção", um "você" ou um "não" que escape vai ao ar para um publico que fala
espanhol. Barrar isso e mais importante que qualquer regra de estilo.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

from .captions import (
    HOOK_CHARS,
    MAX_CAPTION_CHARS,
    MAX_HASHTAGS,
    _now,
    fingerprint,
)

#: Faixa de hashtags. Igual ao perfil irmao: acima de 8 vira spam aos olhos de
#: quem le, e o Instagram ha anos nao premia volume.
TARGET_HASHTAG_RANGE = (3, 8)

#: Piso de tamanho. Diferente do perfil irmao, aqui a legenda curta demais nao e
#: um formato legitimo: o conteudo e educativo e narrativo, e uma linha solta
#: quase sempre significa que a adaptacao ficou pela metade.
MIN_CAPTION_CHARS = 80

#: Marcas de portugues, em dois niveis.
#:
#: FORTES: impossiveis num texto espanhol correto. Acusam sozinhas, sempre --
#: um "você" no meio de espanhol perfeito continua sendo portugues publicado.
MARCAS_PT_FORTES = [
    (re.compile(r"[ãõ]"), "til em vogal (ã/õ)"),
    (re.compile(r"ç"), "cedilha"),
    (re.compile(r"\b\w*ções?\b", re.I), "terminacao -ção/-ções"),
    (re.compile(r"\b(voc[êe]|voc[êe]s)\b", re.I), "'você'"),
    (re.compile(r"\b(n[ãa]o)\b", re.I), "'não'"),
    (re.compile(r"\bent[ãa]o\b", re.I), "'então'"),
    (re.compile(r"\bmuit[oa]s?\b", re.I), "'muito' (es: mucho)"),
    (re.compile(r"\bs[ãa]o\b", re.I), "'são' (es: son)"),
    (re.compile(r"\bsobrancelhas?\b", re.I), "'sobrancelha' (es: ceja)"),
    (re.compile(r"\bfio a fio\b", re.I), "'fio a fio' (es: pelo a pelo)"),
    (re.compile(r"\bpele\b", re.I), "'pele' (es: piel)"),
    (re.compile(r"\bquem\b", re.I), "'quem' (es: quién)"),
    (re.compile(r"\bonde\b", re.I), "'onde' (es: dónde)"),
    (re.compile(r"\b(faz|fazer)\b", re.I), "'fazer' (es: hacer)"),
    (re.compile(r"\bisso\b", re.I), "'isso' (es: eso)"),
    (re.compile(r"\bs(eu|ua)s?\b", re.I), "'seu/sua' (es: su)"),
    (re.compile(r"\btem\b", re.I), "'tem' (es: tiene)"),
]

#: FRACAS: podem ser typo de uma palavra espanhola parecida. Duas ou mais
#: juntas acusam; uma sozinha, num texto claramente espanhol, nao.
#: "mais" e typo plausivel de "más"; "é" de "es".
MARCAS_PT_FRACAS = [
    (re.compile(r"\bmais\b", re.I), "'mais' (es: más)"),
    (re.compile(r"\bé\b"), "'é' (es: es)"),
    (re.compile(r"\baqui\b", re.I), "'aqui' (es: aquí)"),
]

#: Palavras iguais nos dois idiomas. Sem esta lista, "natural", "profesional" e
#: "tecnica" dariam falso positivo o tempo todo. "está" e "saber" entraram aqui
#: depois de reprovarem um texto espanhol correto num teste.
IGUAIS_NOS_DOIS = frozenset(
    {
        "natural", "profesional", "tecnica", "arte", "color", "piel", "esta",
        "ideal", "personal", "digital", "total", "final", "real", "social",
        "saber", "labios", "resultado", "sesion", "tratamiento",
    }
)

#: Sinais de que o texto e mesmo espanhol. Serve de contrapeso para as FRACAS.
MARCAS_ES = re.compile(
    r"ñ|¿|¡|\b(el|la|los|las|un|una|para|con|más|está|están|es|son|tu|su|"
    r"cómo|qué|por qué|cejas?|labios?|piel|técnica|resultado|cuando|porque|"
    r"pero|también|hacer|tiene|muy|aquí)\b",
    re.I,
)

#: Palavras que deixam o gancho pendurado quando o Instagram corta no "mais".
#: A lista herdada e portuguesa ("de", "da", "do") e nao serve aqui.
DANGLING_ES = frozenset(
    {
        "y", "o", "u", "pero", "que", "de", "del", "en", "con", "sin", "por",
        "para", "el", "la", "los", "las", "un", "una", "unos", "unas", "al",
        "se", "ya", "a", "como", "su", "sus", "tu", "tus", "mi", "mis", "si",
        "the", "and", "of",
    }
)

def _sem_acento(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s) if not unicodedata.combining(c))


def detectar_portugues(texto: str) -> list[str]:
    """Trechos que parecem portugues. Lista vazia = nada suspeito.

    Nao classifica o idioma do texto: procura marca de portugues que um leitor
    espanhol notaria. Erra para o lado de acusar -- falso positivo custa uma
    revisao, falso negativo custa portugues publicado.
    """
    achados: list[str] = []

    for regex, rotulo in MARCAS_PT_FORTES:
        for m in regex.finditer(texto):
            if _sem_acento(m.group(0)).lower() in IGUAIS_NOS_DOIS:
                continue
            achados.append(rotulo)

    # as fracas so contam em grupo, ou num texto que nao parece espanhol
    fracas: list[str] = []
    for regex, rotulo in MARCAS_PT_FRACAS:
        for m in regex.finditer(texto):
            if _sem_acento(m.group(0)).lower() in IGUAIS_NOS_DOIS:
                continue
            fracas.append(rotulo)
    if len(fracas) >= 2 or (fracas and len(MARCAS_ES.findall(texto)) < 3):
        achados.extend(fracas)

    vistos: set[str] = set()
    return [a for a in achados if not (a in vistos or vistos.add(a))]


def validate_es(caption: str, hashtags: list[str]) -> list[str]:
    """Avisos que bloqueiam a aprovacao. Lista vazia = pode congelar na fila."""
    avisos: list[str] = []
    texto = (caption or "").strip()
    total = len(texto) + sum(len(t) + 1 for t in hashtags)

    if not texto:
        return ["legenda vazia"]
    if texto != caption:
        avisos.append("legenda tem espaco em branco nas pontas")
    if total > MAX_CAPTION_CHARS:
        avisos.append(f"{total} caracteres (limite do Instagram: {MAX_CAPTION_CHARS})")
    if len(texto) < MIN_CAPTION_CHARS:
        avisos.append(f"legenda com {len(texto)} caracteres: curta demais (minimo {MIN_CAPTION_CHARS})")

    # o gancho: tudo que aparece antes do "mais"
    if len(texto) > HOOK_CHARS:
        gancho = texto[:HOOK_CHARS].rstrip()
        ultima = _sem_acento(gancho.rstrip(",;:").rsplit(" ", 1)[-1].lower())
        if gancho.endswith((",", ";", ":")) or ultima in DANGLING_ES:
            avisos.append("o gancho corta no meio de uma frase antes do 'mais'")

    # portugues vazando: o modo de falha deste projeto
    # a decisao de "uma marca fraca sozinha nao conta" ja foi tomada dentro de
    # detectar_portugues; aqui o que voltar e para acusar
    pt = detectar_portugues(texto)
    if pt:
        avisos.append("parece ter portugues: " + "; ".join(pt[:4]))

    if len(hashtags) > MAX_HASHTAGS:
        avisos.append(f"{len(hashtags)} hashtags (limite do Instagram: {MAX_HASHTAGS})")
    baixo, alto = TARGET_HASHTAG_RANGE
    if not baixo <= len(hashtags) <= alto:
        avisos.append(f"{len(hashtags)} hashtags (alvo: {baixo} a {alto})")
    for tag in hashtags:
        if not tag.startswith("#"):
            avisos.append(f"hashtag sem '#': {tag!r}")
        if " " in tag.strip():
            avisos.append(f"hashtag com espaco: {tag!r}")
        pt_tag = detectar_portugues(tag)
        if pt_tag:
            avisos.append(f"hashtag em portugues: {tag!r}")

    return avisos


def montar(
    short_code: str,
    metadata: dict[str, Any],
    *,
    caption: str,
    hashtags: list[str],
    alt_text: str = "",
    autor: str = "claude-opus-5-sessao",
) -> dict[str, Any]:
    """Registro de legenda, no mesmo formato que o resto do pipeline espera."""
    caption = caption.strip()
    hashtags = [str(t).strip() for t in hashtags]
    return {
        "tiktok_id": short_code,
        "prompt_version": "es-v1",
        "model": autor,
        "generated_at": _now(),
        "input_fingerprint": fingerprint(metadata),
        "caption": caption,
        "hashtags": hashtags,
        "alt_text": alt_text.strip(),
        "warnings": validate_es(caption, hashtags),
        "status": "draft",
        "edited_by_human": True,
        "approved_at": None,
        "history": [],
    }


def texto_completo(registro: dict[str, Any]) -> str:
    """Legenda + hashtags, como vai para a API."""
    caption = (registro.get("caption") or "").strip()
    tags = " ".join(registro.get("hashtags") or [])
    return f"{caption}\n\n{tags}".strip() if tags else caption
