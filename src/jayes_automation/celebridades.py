"""Detecta mencao a figura publica nos posts.

Regra do cliente (25/09/2026): **nenhum post sobre celebridade vai para o
@jamesolaya.es.**

Por que isto sinaliza em vez de decidir sozinho: o caso que motivou a regra
nao tinha nenhuma pista na legenda. Era um carrossel cuja legenda falava de
cicatrizacao e cujo ultimo slide era um meme com a imagem da Cardi B --
invisivel para qualquer regex sobre texto. E a lista de nomes nunca fica
completa: ela ja falhou duas vezes na primeira varredura (Luan Santana citado
como "Luan Santana ja dizia", e Fernanda Torres so pelo @).

Entao a garantia de verdade e o processo -- olhar a midia antes de escrever a
legenda -- e este modulo e a rede que pega o obvio e reduz o que precisa de
olho. Quem decide e a lista de veto em ``data/excluidos-es.json``.
"""

from __future__ import annotations

import re

#: Nomes ja confirmados no acervo, mais os mais provaveis de aparecer num
#: perfil de beleza brasileiro falando com publico latino.
NOMES = re.compile(
    r"\b("
    r"anitta|badgalriri|rihanna|adele|c[eé]line dion|beyonc[eé]?|madonna|shakira|"
    r"taylor swift|cardi ?b|kim kardashian|kylie jenner|selena gomez|billie eilish|"
    r"dua lipa|karol g|bad bunny|maluma|j balvin|thal[ií]a|rosal[ií]a|shak|"
    r"luan santana|ivete sangalo|ludmilla|juliette|gkay|virginia fonseca|"
    r"bruna marquezine|sabrina sato|xuxa|ang[eé]lica|luciano huck|fausta[oõ]|"
    r"silvio santos|fernanda torres|wagner moura|gisele b[uü]ndchen|"
    r"neymar|messi|cristiano ronaldo|vin[ií]cius j[uú]nior|"
    r"trump|tr#mp|bolsonaro|\blula\b|milei|maduro"
    r")\b",
    re.I,
)

#: "a cantora Fulana", "o ator Beltrano" -- funcao publica seguida de nome proprio.
FUNCAO_E_NOME = re.compile(
    r"\b(cantor|cantora|ator|atriz|cantante|actriz|jogador|jogadora|atleta|"
    r"presidente|apresentador|apresentadora|modelo|influencer)\s+"
    r"[A-ZÁÉÍÓÚÂÊÔÃÕÑ][a-záéíóúâêôãõñç]{2,}"
)

#: Premiacao, programa e evento de midia. Um post ancorado nisso e carona em
#: celebridade mesmo sem nomear ninguem.
#:
#: "mundial" saiu daqui: o proprio setor tem o seu. O detector vetou um post em
#: que o James conta que treinou a selecao chinesa para o mundial de
#: Micropigmentacao em Roterda -- credencial, o oposto de fofoca.
MIDIA = re.compile(
    r"\b(oscar|gramm?y|met gala|globo de ouro|bbb|big brother|the voice|"
    r"novela das|carnaval da globo|r[oó]ck in rio|copa do mundo)\b",
    re.I,
)

#: Vocabulario de fofoca: nao acusa sozinho, mas junto com outro sinal indica
#: que o post e sobre a pessoa, nao sobre o trabalho.
FOFOCA = re.compile(
    r"\b(desmontou?|desmonta|feat|f[eé]at|surtou|surtei|amei ela|amei ele|"
    r"ficou linda demais|arrasou|a rainha|diva|namorad[oa] d[aeo])\b",
    re.I,
)


def analisar(texto: str) -> dict[str, list[str]]:
    """Sinais de figura publica no texto. Dicionario vazio = nada encontrado."""
    sinais: dict[str, list[str]] = {}

    nomes = sorted({m.group(0).lower() for m in NOMES.finditer(texto)})
    if nomes:
        sinais["nome"] = nomes

    funcoes = sorted({m.group(0) for m in FUNCAO_E_NOME.finditer(texto)})
    if funcoes:
        sinais["funcao_e_nome"] = funcoes

    midia = sorted({m.group(0).lower() for m in MIDIA.finditer(texto)})
    if midia:
        sinais["midia"] = midia

    # fofoca so conta acompanhada: "arrasou" sozinho descreve o proprio trabalho
    if sinais:
        fofoca = sorted({m.group(0).lower() for m in FOFOCA.finditer(texto)})
        if fofoca:
            sinais["tom_de_fofoca"] = fofoca

    return sinais


def suspeito(texto: str) -> bool:
    """Atalho: vale olhar a midia antes de aproveitar este post?"""
    return bool(analisar(texto))


def descrever(sinais: dict[str, list[str]]) -> str:
    """Motivo legivel, para o relatorio e para a lista de veto."""
    if not sinais:
        return ""
    partes = []
    if "nome" in sinais:
        partes.append("cita " + ", ".join(sinais["nome"]))
    if "funcao_e_nome" in sinais:
        partes.append("cita " + ", ".join(sinais["funcao_e_nome"]))
    if "midia" in sinais:
        partes.append("ancorado em " + ", ".join(sinais["midia"]))
    if "tom_de_fofoca" in sinais:
        partes.append("tom de fofoca (" + ", ".join(sinais["tom_de_fofoca"]) + ")")
    return "; ".join(partes)
