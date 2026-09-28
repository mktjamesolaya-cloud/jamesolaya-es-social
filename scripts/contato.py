#!/usr/bin/env python3
"""Folha de contato: varias midias numa imagem so, para conferir de uma vez.

A rotina de legendas (docs/ROTINA_LEGENDAS.md) exige abrir a midia de cada
post antes de escrever, e isso e a parte cara: um carrossel de dez slides e um
Reel de dois minutos nao cabem em um olhar. Esta ferramenta reduz cada post a
um ou dois quadros e os junta num mosaico.

Existe porque o ``tile`` do ffmpeg erra quando as entradas tem proporcoes
diferentes -- e aqui elas sempre tem: 4:5, 1:1 e 9:16 no mesmo lote. O
resultado eram mosaicos com metade das celulas pretas, que e pior que nao ter
mosaico nenhum: da a impressao de ter conferido o que nao foi conferido.

Uso:
    uv run python scripts/contato.py saida.jpg video.mp4@0.5 foto.jpg ...

Um sufixo ``@fracao`` extrai aquele ponto de um video (0.5 = metade).
"""

from __future__ import annotations

import math
import subprocess
import sys
import tempfile
from pathlib import Path

CELULA = 380
ROTULO = 26


def quadro_de_video(caminho: Path, fracao: float, destino: Path) -> Path:
    """Um quadro do video, em ``fracao`` da duracao."""
    dur = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "csv=p=0",
            str(caminho),
        ],
        capture_output=True,
        text=True,
    ).stdout.strip()
    t = float(dur or 1) * fracao
    subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-loglevel",
            "error",
            "-y",
            "-ss",
            str(t),
            "-i",
            str(caminho),
            "-frames:v",
            "1",
            str(destino),
        ],
        check=True,
    )
    return destino


def montar(saida: Path, entradas: list[str]) -> None:
    from PIL import Image, ImageDraw

    tmp = Path(tempfile.mkdtemp())
    celulas: list[tuple[Image.Image, str]] = []
    for n, bruto in enumerate(entradas):
        alvo, _, frac = bruto.partition("@")
        caminho = Path(alvo)
        if caminho.suffix.lower() in {".mp4", ".mov"}:
            caminho = quadro_de_video(caminho, float(frac or 0.5), tmp / f"{n}.jpg")
        im = Image.open(caminho).convert("RGB")
        im.thumbnail((CELULA, CELULA), Image.LANCZOS)
        fundo = Image.new("RGB", (CELULA, CELULA + ROTULO), (245, 245, 245))
        fundo.paste(im, ((CELULA - im.width) // 2, (CELULA - im.height) // 2))
        ImageDraw.Draw(fundo).text(
            (6, CELULA + 6), f"{n + 1}. {Path(alvo).parent.name[:40]}", fill=(0, 0, 0)
        )
        celulas.append((fundo, alvo))

    colunas = min(4, len(celulas)) or 1
    linhas = math.ceil(len(celulas) / colunas)
    folha = Image.new("RGB", (colunas * CELULA, linhas * (CELULA + ROTULO)), (255, 255, 255))
    for i, (cel, _) in enumerate(celulas):
        folha.paste(cel, ((i % colunas) * CELULA, (i // colunas) * (CELULA + ROTULO)))
    folha.save(saida, quality=88)
    print(f"{saida} ({colunas}x{linhas}, {len(celulas)} midias)")


if __name__ == "__main__":
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    montar(Path(sys.argv[1]), sys.argv[2:])
