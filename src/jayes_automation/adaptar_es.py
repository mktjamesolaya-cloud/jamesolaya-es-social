"""Reedita midia que traz portugues queimado na imagem.

A triagem separa os posts em baldes, mas ela le OCR de amostras e erra nos dois
sentidos. Dois casos reais, ambos descobertos so quando alguem abriu o arquivo:

* ``DXFg5NEB4MS`` estava no balde C por causa de "VEJA ESSE VIDEO / ate o
  final" -- texto que so existia na **capa** do post original, nao no video.
  Publicando pela API a capa sai do proprio video, entao nao havia nada a
  editar.
* ``DULJ5ghDq5X`` estava no balde A com ``textoTela: ''`` e tinha um card de
  encerramento com "AGENDE SUA AVALIACAO" nos ultimos oito segundos.

Dai a forma deste modulo: a decisao de editar e **humana e registrada**, nunca
inferida. Cada entrada de ``data/adaptacoes-es.json`` diz o que foi visto, onde
estava e qual filtro resolve. O arquivo gerado vai para ``media/adaptado-es/``,
que o git ignora -- o que precisa sobreviver e a receita, nao o MP4.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

#: Onde a receita mora, relativo a ``Paths.data``.
ARQUIVO = "adaptacoes-es.json"


class AdaptacaoError(RuntimeError):
    pass


def carregar(caminho: Path) -> dict[str, dict[str, Any]]:
    """As receitas por short_code. Ausencia do arquivo nao e erro."""
    if not caminho.exists():
        return {}
    dados = json.loads(caminho.read_text(encoding="utf-8"))
    return dados.get("adaptacoes") or {}


def comando(origem: Path, destino: Path, receita: dict[str, Any]) -> list[str]:
    """O ffmpeg que a receita descreve.

    Separado de ``aplicar`` para poder ser conferido em teste sem renderizar
    nada: um filtro escrito errado so apareceria depois de dois minutos de
    encode, e pior, so na imagem.
    """
    filtros = (receita.get("filtros") or "").strip()
    if not filtros:
        raise AdaptacaoError(f"receita sem 'filtros': {receita}")
    cmd = [
        "ffmpeg",
        "-nostdin",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(origem),
        "-vf",
        filtros,
    ]
    if receita.get("ate_segundos"):
        cmd += ["-t", str(receita["ate_segundos"])]
    cmd += [
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-crf",
        "20",
        "-pix_fmt",
        "yuv420p",
        # -map_metadata -1 limpa metadado da origem; o arquivo vai para uma
        # Release publica e nao precisa carregar de onde veio.
        "-map_metadata",
        "-1",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        str(destino),
    ]
    return cmd


def aplicar(
    short_code: str,
    origem: Path,
    receita: dict[str, Any],
    destino_raiz: Path,
    *,
    refazer: bool = False,
) -> Path:
    """Renderiza a versao adaptada e devolve o caminho dela.

    Idempotente: se o arquivo ja existe e ``refazer`` e falso, nao reencoda.
    """
    if not origem.exists():
        raise AdaptacaoError(f"{short_code}: origem nao existe: {origem}")
    destino = destino_raiz / short_code / (receita.get("arquivo") or origem.name)
    destino.parent.mkdir(parents=True, exist_ok=True)
    if destino.exists() and not refazer:
        return destino
    if not shutil.which("ffmpeg"):
        raise AdaptacaoError("ffmpeg nao encontrado no PATH")

    resultado = subprocess.run(comando(origem, destino, receita), capture_output=True, text=True)
    if resultado.returncode != 0 or not destino.exists():
        raise AdaptacaoError(
            f"{short_code}: ffmpeg falhou ({resultado.returncode}): "
            f"{resultado.stderr.strip()[:400]}"
        )
    return destino


def substituir(arquivos: list[Path], short_code: str, destino_raiz: Path) -> list[Path]:
    """Troca os arquivos do candidato pelos adaptados, quando existirem.

    Os que nao tem versao adaptada passam intactos, entao um post pode ter um
    slide reeditado e os outros originais.
    """
    pasta = destino_raiz / short_code
    if not pasta.is_dir():
        return arquivos
    return [(pasta / a.name) if (pasta / a.name).exists() else a for a in arquivos]
