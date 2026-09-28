"""Intercala formatos na fila para o feed nao virar blocos do mesmo tipo.

O problema que isto resolve: a fila chega ordenada por score, e score nao
distribui formato. No acervo do @jamesolaya os melhores posts sao em boa parte
foto e carrossel, entao publicar em ordem de score renderia semanas seguidas
sem um unico video -- e depois um bloco de videos no fim.

A restricao real nao e estetica, e de estoque. Contando so o que sai barato e
tem arquivo em disco (25/09/2026): **98 videos contra 228 estaticos**. O ponto
onde os dois acabam no mesmo dia:

    dias = min(V / v, E / (p - v))    com V=98, E=228, p=2 posts/dia
    igualando:  98/v = 228/(2-v)  =>  v = 0,60  =>  163 dias

Isso da ``alvo_video=0.30``, o padrao aqui. Mas o alvo e um botao, nao um
dogma: subir para 0.40 poe mais video no feed e ainda cobre 122 dias, o que
passa de 31/12 com folga. Abaixo de 0.30 o estatico e que acaba primeiro.

    0.50 -> 98 dias    0.40 -> 122 dias    0.30 -> 163 dias

``sugerir_alvo()`` recalcula o equilibrio quando o estoque mudar.

A intercalacao usa divida acumulada em vez de um ciclo fixo: a cada posicao,
soma-se ``alvo_video`` a um contador e, quando ele passa de 1, aquela posicao
pede video. Isso distribui as sobras de forma uniforme em qualquer proporcao,
em vez de agrupar ("VEEE VEEE") como faria um ciclo inteiro.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Protocol

#: Proporcao de video na fila. 0.30 e o equilibrio medido em 25/09/2026
#: (98 videos, 228 estaticos): os dois estoques acabam juntos, em 163 dias.
ALVO_VIDEO_PADRAO = 0.30

#: media_kind considerados video para efeito de mescla
TIPOS_VIDEO = frozenset({"reel", "video"})


class TemFormato(Protocol):
    """Qualquer objeto com ``media_kind`` serve."""

    media_kind: str


def _eh_video(item: Any) -> bool:
    kind = item.get("media_kind") if isinstance(item, dict) else getattr(item, "media_kind", None)
    return str(kind or "").lower() in TIPOS_VIDEO


def sugerir_alvo(n_videos: int, n_estaticos: int, posts_por_dia: int = 2) -> float:
    """Proporcao de video que faz os dois estoques acabarem no mesmo dia.

    Resolve ``V/v == E/(p-v)`` para ``v`` e devolve ``v/p``. Sem isso, um dos
    dois acaba antes e o outro fica orfao.
    """
    if n_videos <= 0:
        return 0.0
    if n_estaticos <= 0:
        return 1.0
    v = (posts_por_dia * n_videos) / (n_videos + n_estaticos)
    return round(v / posts_por_dia, 4)


def duracao_estimada(
    n_videos: int, n_estaticos: int, alvo_video: float, posts_por_dia: int = 2
) -> int:
    """Quantos dias a fila cobre nesse alvo, antes de um dos estoques acabar."""
    if posts_por_dia <= 0:
        return 0
    por_dia_video = posts_por_dia * alvo_video
    por_dia_estatico = posts_por_dia - por_dia_video
    dias_video = n_videos / por_dia_video if por_dia_video > 0 else float("inf")
    dias_estatico = n_estaticos / por_dia_estatico if por_dia_estatico > 0 else float("inf")
    return int(min(dias_video, dias_estatico))


def intercalar(candidatos: Sequence[Any], alvo_video: float = ALVO_VIDEO_PADRAO) -> list[Any]:
    """Reordena ``candidatos`` alternando video e estatico.

    Preserva a ordem de score **dentro** de cada tipo: o melhor video ainda sai
    antes dos outros videos. So a intercalacao entre tipos muda.

    Quando um dos estoques acaba, o restante sai na ordem original -- nao ha o
    que alternar, e segurar conteudo bom para manter um padrao seria pior que o
    padrao quebrado.
    """
    if not candidatos:
        return []
    alvo = min(max(float(alvo_video), 0.0), 1.0)

    videos = [c for c in candidatos if _eh_video(c)]
    estaticos = [c for c in candidatos if not _eh_video(c)]

    saida: list[Any] = []
    divida = 0.0
    iv = ie = 0
    while iv < len(videos) or ie < len(estaticos):
        divida += alvo
        quer_video = divida >= 1.0
        # se acabou o estoque preferido, usa o outro sem travar a fila
        if quer_video and iv < len(videos):
            saida.append(videos[iv])
            iv += 1
            divida -= 1.0
        elif not quer_video and ie < len(estaticos):
            saida.append(estaticos[ie])
            ie += 1
        elif iv < len(videos):
            saida.append(videos[iv])
            iv += 1
            divida = max(0.0, divida - 1.0)
        else:
            saida.append(estaticos[ie])
            ie += 1
    return saida


def resumir(fila: Sequence[Any], posts_por_dia: int = 2) -> dict[str, Any]:
    """Diagnostico da fila mesclada, para o `doctor` e o relatorio."""
    tipos: dict[str, int] = {}
    for item in fila:
        kind = (
            item.get("media_kind") if isinstance(item, dict) else getattr(item, "media_kind", "?")
        )
        k = str(kind or "?").lower()
        tipos[k] = tipos.get(k, 0) + 1
    n_video = sum(v for k, v in tipos.items() if k in TIPOS_VIDEO)

    # maior sequencia do mesmo tipo: e o que denuncia bloco no feed
    maior = atual = 0
    anterior: bool | None = None
    for item in fila:
        ev = _eh_video(item)
        atual = atual + 1 if ev is anterior else 1
        anterior = ev
        maior = max(maior, atual)

    return {
        "total": len(fila),
        "por_tipo": tipos,
        "proporcao_video": round(n_video / len(fila), 4) if fila else 0.0,
        "maior_sequencia_mesmo_tipo": maior,
        "dias_cobertos": len(fila) // posts_por_dia if posts_por_dia else 0,
    }
