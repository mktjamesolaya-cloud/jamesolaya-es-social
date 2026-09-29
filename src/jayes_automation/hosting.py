"""Publish normalized videos as GitHub Release assets.

The 1.3 GB of media is deliberately outside git, so the CI runner has no copy of
it. Release assets give every file a public URL that Meta can fetch directly via
``video_url`` -- the runner never transfers a byte, and the repository history
stays JSON and CSV only.

Uploads go through the ``gh`` CLI so we inherit its authentication instead of
managing a token here.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from functools import lru_cache
from pathlib import Path
from typing import Any

from .net import ssl_context

RELEASE_NOTES = (
    "Videos normalizados para publicacao como Reels.\n\n"
    "Assets gerados por `jayes host-media`. Nao editar a mao: a fila em "
    "data/queue.json referencia estes arquivos por nome e sha256."
)


class HostingError(RuntimeError):
    """A release or asset operation failed."""


#: Marcas de falha passageira de rede. Nao sao erro de uso: sao a conexao
#: caindo no meio de um upload de dezenas de MB. Repetir resolve; desistir
#: derruba um lote inteiro que ja estava quase todo hospedado.
TRANSITORIOS = (
    "operation timed out",
    "connection reset",
    "unexpected EOF",
    "i/o timeout",
    "TLS handshake timeout",
    "server misbehaving",
)
TENTATIVAS = 4


def _transitorio(erro: str) -> bool:
    baixo = erro.lower()
    return any(marca.lower() in baixo for marca in TRANSITORIOS)


def _gh(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    espera = 3
    for tentativa in range(1, TENTATIVAS + 1):
        try:
            result = subprocess.run(["gh", *args], capture_output=True, text=True, check=False)
        except FileNotFoundError as error:
            raise HostingError(
                "O CLI 'gh' nao esta instalado. Instale com 'brew install gh' e rode "
                "'gh auth login'."
            ) from error
        if result.returncode == 0 or not check:
            break
        if tentativa < TENTATIVAS and _transitorio(result.stderr):
            time.sleep(espera)
            espera *= 2
            continue
        break
    if check and result.returncode != 0:
        erro = result.stderr.strip()
        # 404 no host de upload quase nunca e "release nao existe": e a conta
        # ativa do gh nao ter permissao de escrita no repo. A maquina tem tres
        # contas logadas e o 'gh auth switch' e global, entao qualquer outro
        # terminal pode trocar a ativa no meio do trabalho. Aconteceu em
        # 28/09/2026 e a mensagem crua nao dizia nada disso.
        if "404" in erro and "uploads.github.com" in erro:
            quem = subprocess.run(
                ["gh", "api", "user", "-q", ".login"], capture_output=True, text=True
            ).stdout.strip()
            raise HostingError(
                f"gh {' '.join(args[:2])} devolveu 404 no upload. A conta ativa do gh e "
                f"'{quem or '?'}', que provavelmente nao tem permissao de escrita neste repo. "
                f"Rode 'gh auth switch --user <dono-do-repo>'. Erro original: {erro}"
            )
        raise HostingError(f"gh {' '.join(args)} falhou: {erro}")
    return result


@lru_cache(maxsize=1)
def repo_slug() -> str:
    """``owner/repo``, do ambiente de CI ou do remote local.

    Em cache porque ``upload_asset`` chama isto uma vez por arquivo: um lote de
    oito posts com carrossel vira dezenas de idas a api.github.com para
    descobrir algo que nao muda durante a execucao. Foi uma dessas chamadas que
    estourou por timeout de rede e derrubou um lote inteiro em 29/09/2026,
    depois de metade dos arquivos ja ter subido.
    """
    from_env = os.environ.get("GITHUB_REPOSITORY")
    if from_env:
        return from_env
    result = _gh("repo", "view", "--json", "nameWithOwner")
    return json.loads(result.stdout)["nameWithOwner"]


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def asset_url(tag: str, asset_name: str, slug: str | None = None) -> str:
    slug = slug or repo_slug()
    return f"https://github.com/{slug}/releases/download/{tag}/{asset_name}"


def release_exists(tag: str) -> bool:
    return _gh("release", "view", tag, check=False).returncode == 0


def ensure_release(tag: str) -> str:
    """Create the release if it is missing. Safe to call repeatedly."""
    if not release_exists(tag):
        _gh(
            "release",
            "create",
            tag,
            "--title",
            f"Midia {tag}",
            "--notes",
            RELEASE_NOTES,
        )
    return tag


def assets_da_release(tag: str, *, recarregar: bool = False) -> dict[str, int]:
    """``{nome: bytes}`` do que ja esta hospedado. Uma chamada, em cache.

    Existe para tornar a hospedagem retomavel. Um lote de oito posts sao dezenas
    de arquivos e varios minutos de upload; uma queda de rede no meio derrubava
    tudo e nada era gravado na fila, mesmo com quase todos os arquivos ja la em
    cima. Sabendo o que ja existe, repetir o comando continua de onde parou em
    vez de comecar do zero.
    """
    if recarregar or tag not in _ASSETS_CACHE:
        saida = _gh("release", "view", tag, "--json", "assets", check=False)
        if saida.returncode != 0:
            return {}
        dados = json.loads(saida.stdout or "{}").get("assets") or []
        _ASSETS_CACHE[tag] = {a["name"]: int(a.get("size") or 0) for a in dados}
    return _ASSETS_CACHE[tag]


_ASSETS_CACHE: dict[str, dict[str, int]] = {}


def upload_asset(path: Path, tag: str, *, slug: str | None = None) -> dict[str, Any]:
    """Upload one normalized MP4 and return everything the queue needs to cite it."""
    if not path.exists():
        raise HostingError(f"Arquivo nao existe: {path}")
    ensure_release(tag)

    tamanho = path.stat().st_size
    ja_la = assets_da_release(tag)
    if ja_la.get(path.name) == tamanho:
        # Mesmo nome e mesmo tamanho: nao ha o que subir de novo. O sha256 ainda
        # e recalculado localmente, entao a fila continua carregando a impressao
        # digital do arquivo que esta em disco.
        slug = slug or repo_slug()
        return {
            "release_tag": tag,
            "asset_name": path.name,
            "asset_url": asset_url(tag, path.name, slug),
            "sha256": sha256_of(path),
            "bytes": tamanho,
            "reaproveitado": True,
        }

    # --clobber makes re-hosting a re-normalized file idempotent.
    try:
        _gh("release", "upload", tag, str(path), "--clobber")
    except HostingError as erro:
        # O --clobber do gh apaga e sobe de novo, e em lote grande ele perde a
        # corrida consigo mesmo: a API responde 422 'ReleaseAsset.name already
        # exists' com o asset ja no lugar. Apagar explicitamente e repetir
        # resolve. Sem isto, um lote de oito posts morria no meio e nada era
        # gravado na fila, apesar de metade dos arquivos ja estar hospedada.
        if "already exists" not in str(erro):
            raise
        _gh("release", "delete-asset", tag, path.name, "--yes", check=False)
        _gh("release", "upload", tag, str(path))
    _ASSETS_CACHE.setdefault(tag, {})[path.name] = tamanho
    slug = slug or repo_slug()
    return {
        "release_tag": tag,
        "asset_name": path.name,
        "asset_url": asset_url(tag, path.name, slug),
        "sha256": sha256_of(path),
        "bytes": tamanho,
    }


def verify_asset(
    url: str, *, expected_bytes: int | None = None, timeout: int = 30
) -> dict[str, Any]:
    """Confirm the asset is publicly reachable and the right size.

    GitHub answers the release-download URL with a 302 to a storage host, which
    is exactly what Meta's fetcher has to follow -- so this doubles as a check
    that the ``video_url`` path will work.

    ``final_url`` is returned for diagnostics only and must never be stored: it
    is a signed URL that expires within the hour. The queue keeps the stable
    ``github.com/.../releases/download/...`` form, which mints a fresh signature
    on every request -- including Meta's, at container-creation time.
    """
    request = urllib.request.Request(url, method="HEAD")
    try:
        with urllib.request.urlopen(request, timeout=timeout, context=ssl_context()) as response:
            length = response.headers.get("Content-Length")
            size = int(length) if length else None
            ok = response.status == 200 and (expected_bytes is None or size == expected_bytes)
            return {
                "ok": ok,
                "status": response.status,
                "bytes": size,
                "final_url": response.geturl(),
                "redirected": response.geturl() != url,
            }
    except urllib.error.HTTPError as error:
        return {"ok": False, "status": error.code, "bytes": None, "error": str(error.reason)}
    except urllib.error.URLError as error:
        return {"ok": False, "status": None, "bytes": None, "error": str(error.reason)}


def fetch_asset(url: str, dest: Path, *, expected_sha256: str | None = None) -> Path:
    """Download an asset locally. Only used by the resumable upload fallback."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        with (
            urllib.request.urlopen(url, timeout=300, context=ssl_context()) as response,
            dest.open("wb") as handle,
        ):
            while block := response.read(1024 * 1024):
                handle.write(block)
    except (urllib.error.HTTPError, urllib.error.URLError) as error:
        raise HostingError(f"Nao consegui baixar {url}: {error}") from error
    if expected_sha256:
        actual = sha256_of(dest)
        if actual != expected_sha256:
            dest.unlink(missing_ok=True)
            raise HostingError(
                f"sha256 divergente para {dest.name}: esperado {expected_sha256[:12]}, "
                f"veio {actual[:12]}. O asset foi trocado ou corrompido."
            )
    return dest
