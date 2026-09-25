"""Le a pasta de midia do @jamesolaya e monta a lista de candidatos.

Substitui o ``tiktok.py`` do projeto irmao: la a origem era o perfil do TikTok,
aqui e uma pasta local ja baixada e ja triada.

Tres arquivos alimentam isto, todos produzidos no projeto FormatosValidados:

* ``melhores-conteudos/<pasta>/`` -- os arquivos (video.mp4, imagem.jpg,
  slide-NN.jpg, capa.jpg, legenda.txt, info.json);
* ``manifesto-*.json`` -- a ficha de cada post (score, formato, metricas);
* ``triagem-es.json`` -- o veredito de quanto trabalho cada post da para
  reaproveitar em espanhol (baldes A, B, C, D).

O que sai daqui ainda **nao** e item de fila: e candidato. Virar item exige
legenda em espanhol aprovada e midia hospedada, que sao passos proprios.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: Baldes que saem baratos: so reescrever a legenda, ou uma frase curta na arte.
#: O 'C medio' e o 'C pesado' exigem refazer arte e ficam de fora por padrao.
BALDES_BARATOS = frozenset({"A", "B"})
ESFORCO_BARATO = frozenset({"leve"})

#: Limite da Meta. Carrossel com mais slides e cortado, nao rejeitado, porque
#: os primeiros 10 costumam carregar a narrativa inteira.
MAX_SLIDES = 10


class IngestError(RuntimeError):
    pass


@dataclass
class Candidato:
    """Um post do acervo, pronto para virar item de fila."""

    short_code: str
    posicao: int
    pasta: str
    media_kind: str  # reel | image | carousel
    arquivos: list[Path]  # o que precisa ser hospedado, na ordem
    capa: Path | None
    legenda_original: str
    score: float
    formato: str
    editorial: str | None
    balde: str
    data: str
    duracao: float | None = None
    transcricao: str = ""
    texto_na_tela: str = ""
    observacoes: list[str] = field(default_factory=list)

    @property
    def slides(self) -> int:
        return len(self.arquivos)

    def to_dict(self) -> dict[str, Any]:
        d = {k: v for k, v in self.__dict__.items()}
        d["arquivos"] = [str(p) for p in self.arquivos]
        d["capa"] = str(self.capa) if self.capa else None
        return d


def _kind_de(formato: str) -> str:
    if formato == "Reel" or formato == "Vídeo de feed":
        return "reel"
    if formato == "Carrossel":
        return "carousel"
    return "image"


def _arquivos_de(pasta: Path, kind: str) -> tuple[list[Path], Path | None]:
    """Os arquivos a hospedar, na ordem em que o usuario vai ve-los."""
    capa = pasta / "capa.jpg"
    capa = capa if capa.exists() else None

    if kind == "reel":
        video = pasta / "video.mp4"
        return ([video] if video.exists() else []), capa

    if kind == "carousel":
        slides = sorted(pasta.glob("slide-*.jpg"))
        if slides:
            # A Meta corta todos os slides pela proporcao do PRIMEIRO, entao a
            # ordem aqui decide o enquadramento do carrossel inteiro.
            return slides[:MAX_SLIDES], capa
        # carrossel cujos slides nao vieram: cai para a imagem principal
        principal = pasta / "imagem.jpg"
        return ([principal] if principal.exists() else []), capa

    principal = pasta / "imagem.jpg"
    return ([principal] if principal.exists() else []), capa


def carregar(
    raiz_midia: Path,
    dir_dados: Path,
    *,
    baldes: frozenset[str] = BALDES_BARATOS,
    esforcos: frozenset[str] = ESFORCO_BARATO,
) -> list[Candidato]:
    """Monta a lista de candidatos, do melhor para o pior.

    ``baldes`` e ``esforcos`` controlam o corte: o padrao pega A, B e o C leve.
    Passar ``baldes={'A','B','C'}`` e ``esforcos={'leve','médio'}`` amplia a
    fila ao custo de trabalho de design.
    """
    triagem_path = dir_dados / "triagem-es.json"
    if not triagem_path.exists():
        raise IngestError(f"Triagem ausente: {triagem_path}")
    triagem = json.loads(triagem_path.read_text(encoding="utf-8"))

    # os tres manifestos, deduplicados por pasta (o ultimo tem a ficha mais nova)
    fichas: dict[str, dict[str, Any]] = {}
    manifestos = sorted(dir_dados.glob("manifesto-*.json"))
    if not manifestos:
        raise IngestError(f"Nenhum manifesto-*.json em {dir_dados}")
    for arq in manifestos:
        for it in json.loads(arq.read_text(encoding="utf-8")).get("itens", []):
            fichas[it["pasta"]] = it

    candidatos: list[Candidato] = []
    descartados: dict[str, int] = {}

    def descarta(motivo: str) -> None:
        descartados[motivo] = descartados.get(motivo, 0) + 1

    for t in triagem:
        balde = t.get("balde")
        if balde not in baldes and not (balde == "C" and t.get("esforco") in esforcos):
            descarta(f"balde {balde}")
            continue

        pasta = raiz_midia / t["pasta"]
        if not pasta.is_dir():
            descarta("pasta ausente no disco")
            continue

        kind = _kind_de(t["formato"])
        arquivos, capa = _arquivos_de(pasta, kind)
        if not arquivos:
            descarta(f"sem arquivo de midia ({kind})")
            continue
        if kind == "carousel" and len(arquivos) < 2:
            # um "carrossel" com 1 arquivo e imagem unica para a API
            kind = "image"

        legenda = ""
        leg = pasta / "legenda.txt"
        if leg.exists():
            legenda = leg.read_text(encoding="utf-8").strip()

        ficha = fichas.get(t["pasta"], {})
        candidatos.append(
            Candidato(
                short_code=t["url"].rstrip("/").rsplit("/", 1)[-1],
                posicao=t["posicao"],
                pasta=t["pasta"],
                media_kind=kind,
                arquivos=arquivos,
                capa=capa,
                legenda_original=legenda,
                score=float(t.get("score") or ficha.get("score") or 0),
                formato=t["formato"],
                editorial=t.get("editorial"),
                balde=balde,
                data=t.get("data") or ficha.get("data") or "",
                duracao=t.get("duracao"),
                transcricao=t.get("transcricao") or "",
                texto_na_tela=t.get("textoTela") or "",
                observacoes=list(t.get("observacoes") or []),
            )
        )

    candidatos.sort(key=lambda c: -c.score)
    carregar.ultimo_descarte = descartados  # type: ignore[attr-defined]
    return candidatos


def resumir(candidatos: list[Candidato]) -> dict[str, Any]:
    """Diagnostico para o CLI e para o doctor."""
    por_kind: dict[str, int] = {}
    por_balde: dict[str, int] = {}
    for c in candidatos:
        por_kind[c.media_kind] = por_kind.get(c.media_kind, 0) + 1
        por_balde[c.balde] = por_balde.get(c.balde, 0) + 1
    arquivos = sum(c.slides for c in candidatos)
    return {
        "candidatos": len(candidatos),
        "por_tipo": por_kind,
        "por_balde": por_balde,
        "arquivos_a_hospedar": arquivos,
        "bytes_a_hospedar": sum(
            p.stat().st_size for c in candidatos for p in c.arquivos if p.exists()
        ),
        "com_musica_estrangeira": sum(
            1 for c in candidatos if c.media_kind == "reel" and c.observacoes
        ),
    }
