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


def redesenhar_texto(origem: Path, destino: Path, receita: dict[str, Any]) -> None:
    """Reescreve um bloco de texto sobre uma imagem de fundo chapado.

    Serve para os cards tipograficos: fundo de cor unica, uma frase em portugues
    e nada mais. Apagar e redesenhar sai melhor que qualquer tentativa de cobrir
    letra por letra, e o resultado fica indistinguivel do original quando a
    fonte e proxima.

    Nao serve para texto sobre foto -- ali o fundo nao e reconstituivel, e o
    caminho e o ``delogo`` do ffmpeg.
    """
    from PIL import Image, ImageDraw, ImageFont  # so o Mac tem; o runner nunca chama isto

    im = Image.open(origem).convert("RGB")
    desenho = ImageDraw.Draw(im)
    caixa = receita["apagar"]
    amostra = receita.get("cor_fundo_em") or [20, 20]
    desenho.rectangle(caixa, fill=im.getpixel(tuple(amostra)))

    fonte = ImageFont.truetype(
        receita["fonte"], receita["tamanho"], index=receita.get("fonte_indice", 0)
    )
    x, y = receita["texto_em"]
    for i, linha in enumerate(receita["linhas"]):
        desenho.text((x, y), linha, font=fonte, fill=tuple(receita.get("cor", [0, 0, 0])))
        if i == len(receita["linhas"]) - 1 and receita.get("ponto_final_cor"):
            desenho.text(
                (x + desenho.textlength(linha, font=fonte), y),
                ".",
                font=fonte,
                fill=tuple(receita["ponto_final_cor"]),
            )
        y += receita["altura_linha"]
    destino.parent.mkdir(parents=True, exist_ok=True)
    im.save(destino, quality=95)


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

    if receita.get("tipo") == "imagem_texto":
        redesenhar_texto(origem, destino, receita)
        if not destino.exists():
            raise AdaptacaoError(f"{short_code}: nao consegui gravar {destino}")
        return destino

    if receita.get("tipo") == "legenda_queimada":
        queimar_legenda(origem, destino, receita, altura=int(receita.get("altura") or 1280))
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


# ---------------------------------------------------------------------------
# Legenda em espanhol queimada no video
# ---------------------------------------------------------------------------
#
# Para os Reels em que o James fala em portugues. A voz continua a dele --
# dublar com IA foi considerado e descartado pelo cliente: a voz nao seria a
# dele e o passo nao caberia no pipeline automatico.
#
# A traducao e escrita a mao, segmento a segmento, e fica registrada na receita.
# Nao da para automatizar: a transcricao do Whisper vem com erros que so quem
# conhece o assunto desfaz. No piloto ele transcreveu "fio a fio no dermografo"
# como "Fiofilcundermografo" e "residual" como "riso dual".

#: Estilo da legenda. Branco com contorno preto e o padrao do proprio perfil, e
#: sobrevive a qualquer fundo -- pele clara, luva preta ou clinica iluminada.
ESTILO_ASS = (
    "Style: Es,Arial,{corpo},&H00FFFFFF,&H000000FF,&H00000000,&H00000000,"
    "-1,0,0,0,100,100,0,0,{tipo_borda},{borda},0,2,20,20,{margem},1"
)

#: Altura da legenda como fracao da altura do video, medida a partir de baixo.
#:
#: So se legenda Reel que NAO tenha legenda queimada. Tentei cobrir a legenda
#: automatica em portugues de um deles com caixa opaca por tras da traducao, e
#: nao funciona: a antiga tem de uma a tres linhas, muda de altura a cada fala,
#: e sobra portugues acima ou abaixo. Esses Reels sao descartados.
MARGEM_PADRAO = 0.14


def _tempo_ass(segundos: float) -> str:
    h, resto = divmod(max(0.0, float(segundos)), 3600)
    m, s = divmod(resto, 60)
    return f"{int(h)}:{int(m):02d}:{s:05.2f}"


def montar_ass(segmentos: list[dict[str, Any]], altura: int = 1280, **opcoes: Any) -> str:
    """Arquivo .ass com a legenda em espanhol, ja posicionada e estilizada.

    O corpo da fonte sai da altura do video para a legenda ocupar a mesma
    proporcao em 720x1280 e em 1080x1920.
    """
    corpo = max(18, round(altura * (opcoes.get("corpo") or 0.036)))
    # BorderStyle 3 desenha uma caixa opaca atras do texto; 1 desenha so o
    # contorno. A caixa existe para tapar legenda em portugues ja queimada, e
    # por isso vem com borda larga: ela precisa ser mais alta e mais larga que
    # a linha antiga para cobri-la.
    estilo = ESTILO_ASS.format(
        corpo=corpo,
        borda=max(2, round(corpo * (0.55 if opcoes.get("cobrir") else 0.12))),
        margem=round(altura * (opcoes.get("margem") or MARGEM_PADRAO)),
        tipo_borda=3 if opcoes.get("cobrir") else 1,
    )
    linhas = [
        "[Script Info]",
        "ScriptType: v4.00+",
        "WrapStyle: 0",
        "ScaledBorderAndShadow: yes",
        f"PlayResX: {round(altura * 0.5625)}",
        f"PlayResY: {altura}",
        "",
        "[V4+ Styles]",
        "Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,"
        "BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,"
        "BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding",
        estilo,
        "",
        "[Events]",
        "Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text",
    ]
    minimo = int(opcoes.get("linhas_minimas") or 0)
    for s in segmentos:
        texto = str(s["txt"]).replace("\n", "\\N")
        # Com caixa opaca, o que cobre a legenda antiga e a ALTURA da caixa, e
        # ela acompanha o numero de linhas. Legenda automatica em portugues tem
        # duas ou tres linhas; se a traducao couber em uma, sobra portugues
        # aparecendo por baixo. Completar com linhas vazias iguala a altura.
        if minimo:
            faltam = minimo - (texto.count("\\N") + 1)
            if faltam > 0:
                texto = "\\N" * faltam + texto
        linhas.append(
            f"Dialogue: 0,{_tempo_ass(s['ini'])},{_tempo_ass(s['fim'])},Es,,0,0,0,,{texto}"
        )
    return "\n".join(linhas) + "\n"


def queimar_legenda(
    origem: Path, destino: Path, receita: dict[str, Any], *, altura: int = 1280
) -> None:
    """Grava o video com a legenda em espanhol embutida."""
    segmentos = receita.get("segmentos") or []
    if not segmentos:
        raise AdaptacaoError("receita de legenda sem 'segmentos'")
    destino.parent.mkdir(parents=True, exist_ok=True)
    ass = destino.parent / f"{destino.stem}.ass"
    ass.write_text(
        montar_ass(
            segmentos,
            altura,
            cobrir=receita.get("cobrir"),
            linhas_minimas=receita.get("linhas_minimas"),
            margem=receita.get("margem"),
            corpo=receita.get("corpo"),
        ),
        encoding="utf-8",
    )
    # O caminho do .ass entra escapado: o filtro subtitles usa ':' como
    # separador de opcao, entao um caminho absoluto cru quebra o filtro.
    caminho = str(ass).replace("\\", "/").replace(":", r"\:")
    resultado = subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(origem),
            "-vf",
            f"subtitles='{caminho}'",
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "20",
            "-pix_fmt",
            "yuv420p",
            "-map_metadata",
            "-1",
            "-c:a",
            "copy",
            str(destino),
        ],
        capture_output=True,
        text=True,
    )
    if resultado.returncode != 0 or not destino.exists():
        raise AdaptacaoError(
            f"ffmpeg falhou ao queimar a legenda ({resultado.returncode}): "
            f"{resultado.stderr.strip()[:400]}"
        )
