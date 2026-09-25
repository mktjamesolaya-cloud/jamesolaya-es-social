"""Despacho por tipo de midia no publisher, e o caso dos filhos orfaos.

O cenario que mais preocupa nao e o carrossel feliz: e o que morre entre criar
o ultimo slide e criar o pai. Cada slide ja consumiu cota e nao tem como ser
apagado, entao uma reexecucao precisa reaproveita-los.
"""

import argparse
import contextlib
import io as _io
import json
from pathlib import Path

import pytest

from jayes_automation import publisher as pub
from jayes_automation.instagram import PermanentError


class Falso:
    """Publisher de mentira que registra chamadas e pode falhar sob comando."""

    def __init__(self, falhar_no_pai=False):
        self.chamadas: list[str] = []
        self.slides_criados = 0
        self.falhar_no_pai = falhar_no_pai

    def create_container_from_url(self, video_url, caption, **kw):
        self.chamadas.append("reel")
        return {"id": "cont-reel"}

    def create_image_container(self, image_url, caption, **kw):
        self.chamadas.append("image")
        self.ultimo_alt = kw.get("alt_text")
        return {"id": "cont-img"}

    def create_carousel_item(self, url, **kw):
        self.slides_criados += 1
        self.chamadas.append(f"slide:{url}")
        return {"id": f"filho-{self.slides_criados}"}

    def create_carousel_container(self, children, caption):
        self.chamadas.append("pai")
        if self.falhar_no_pai:
            raise PermanentError("pai falhou de proposito")
        self.filhos_recebidos = list(children)
        return {"id": "cont-car"}

    def wait_until_ready(self, container_id, **kw):
        self.chamadas.append(f"wait:{container_id}")

    def publish(self, container_id):
        self.chamadas.append(f"publish:{container_id}")
        return {"id": "media-1"}

    def permalink(self, media_id):
        return f"https://instagram.com/p/{media_id}"

    def container_status(self, container_id):
        return {"status_code": "FINISHED"}

    def content_publishing_limit(self):
        return {"quota_usage": 0, "config": {"quota_total": 100}}

    def list_recent_media(self, limit=10):
        return []


@pytest.fixture
def paths(tmp_path: Path):
    class P:
        publish_log = tmp_path / "log.jsonl"
    return P()


def item_base(kind, **media):
    return {
        "id": f"q_{kind}",
        "status": "publishing",
        "caption": "hola",
        "history": [],
        "media": {"kind": kind, **media},
    }


# --- media_kind -----------------------------------------------------------

def test_item_antigo_sem_kind_e_tratado_como_reel():
    assert pub.media_kind({"id": "x", "media": {}}) == "reel"


def test_kind_desconhecido_falha_claramente():
    with pytest.raises(PermanentError, match="desconhecido"):
        pub.media_kind({"id": "x", "media": {"kind": "story"}})


# --- despacho -------------------------------------------------------------

def test_imagem_usa_o_container_de_imagem(paths):
    ig = Falso()
    it = item_base("image", asset_url="https://x/y.jpg", alt_text="ceja")
    pub.publish_item(it, ig, paths)
    assert "image" in ig.chamadas
    assert "reel" not in ig.chamadas
    assert ig.ultimo_alt == "ceja"
    assert it["status"] == "published"


def test_reel_continua_no_caminho_antigo(paths):
    ig = Falso()
    it = item_base("reel", asset_url="https://x/v.mp4")
    pub.publish_item(it, ig, paths)
    assert "reel" in ig.chamadas
    assert it["status"] == "published"


def test_carrossel_cria_slides_e_depois_o_pai_nessa_ordem(paths):
    ig = Falso()
    it = item_base("carousel", assets=[
        {"asset_url": "https://x/1.jpg"},
        {"asset_url": "https://x/2.jpg"},
        {"asset_url": "https://x/3.mp4", "is_video": True},
    ])
    pub.publish_item(it, ig, paths)
    assert ig.chamadas[:4] == [
        "slide:https://x/1.jpg", "slide:https://x/2.jpg", "slide:https://x/3.mp4", "pai",
    ]
    assert ig.filhos_recebidos == ["filho-1", "filho-2", "filho-3"]
    assert it["status"] == "published"


def test_carrossel_sem_assets_falha_antes_de_chamar_a_api(paths):
    ig = Falso()
    with pytest.raises(PermanentError, match="media.assets"):
        pub.publish_item(item_base("carousel"), ig, paths)
    assert ig.chamadas == []


# --- filhos orfaos: o caso caro -------------------------------------------

def test_filhos_sao_persistidos_antes_do_pai(paths):
    """Cada slide e gravado assim que criado, nao no fim."""
    ig = Falso()
    it = item_base("carousel", assets=[{"asset_url": f"https://x/{i}.jpg"} for i in range(3)])
    salvos: list[list[str]] = []
    pub.publish_item(it, ig, paths, persist=lambda: salvos.append(list(it["carousel_children"])))
    # persistiu depois de cada filho: 1, 2, 3
    assert salvos == [["filho-1"], ["filho-1", "filho-2"], ["filho-1", "filho-2", "filho-3"]]


def test_reexecucao_reaproveita_os_filhos_em_vez_de_criar_outra_leva(paths):
    """O cenario que custa cota: morreu antes do pai, roda de novo."""
    ig = Falso(falhar_no_pai=True)
    it = item_base("carousel", assets=[{"asset_url": f"https://x/{i}.jpg"} for i in range(3)])
    with pytest.raises(PermanentError):
        pub.publish_item(it, ig, paths, persist=lambda: None)
    assert ig.slides_criados == 3
    assert it["carousel_children"] == ["filho-1", "filho-2", "filho-3"]

    # segunda tentativa: nenhum slide novo, so o pai
    ig2 = Falso()
    ig2.slides_criados = 0
    pub.publish_item(it, ig2, paths, persist=lambda: None)
    assert ig2.slides_criados == 0, "recriou slides que ja existiam e pagou cota de novo"
    assert ig2.filhos_recebidos == ["filho-1", "filho-2", "filho-3"]


def test_container_ja_criado_nao_e_recriado(paths):
    ig = Falso()
    it = item_base("image", asset_url="https://x/y.jpg")
    it["container_id"] = "cont-antigo"
    pub.publish_item(it, ig, paths)
    assert "image" not in ig.chamadas
    assert "publish:cont-antigo" in ig.chamadas


# --- log ------------------------------------------------------------------

def test_log_registra_o_tipo_publicado(paths):
    ig = Falso()
    pub.publish_item(item_base("image", asset_url="https://x/y.jpg"), ig, paths)
    bruto = paths.publish_log.read_text().splitlines()
    linhas = [json.loads(linha) for linha in bruto if linha.strip()]
    assert linhas[-1]["media_kind"] == "image"


# --- doctor: a porta antes de ir ao ar ------------------------------------
#
# Estes exercitam cmd_doctor de verdade, capturando o JSON que ele imprime.
# Reimplementar a regra no teste nao provaria nada sobre o cli.

def roda_doctor(tmp_path, media, monkeypatch):
    from jayes_automation import cli

    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    item = {
        "id": "q1", "status": "scheduled", "caption": "hola",
        "scheduled_at": "2026-10-01T12:00:00-06:00", "media": media, "history": [],
    }
    (tmp_path / "data" / "queue.json").write_text(
        json.dumps({"version": 2, "items": [item]}), encoding="utf-8"
    )
    monkeypatch.delenv("PUBLISH_ENABLED", raising=False)
    args = argparse.Namespace(root=str(tmp_path), check_assets=False)
    buf = _io.StringIO()
    with contextlib.redirect_stdout(buf):
        cli.cmd_doctor(args)
    return json.loads(buf.getvalue())


def problemas_com(tmp_path, media, monkeypatch):
    saida = roda_doctor(tmp_path, media, monkeypatch)
    return [p for p in saida.get("problemas", []) if "q1" in p]


def test_doctor_aceita_carrossel_valido(tmp_path, monkeypatch):
    media = {"kind": "carousel", "assets": [{"asset_url": "a"}, {"asset_url": "b"}]}
    assert problemas_com(tmp_path, media, monkeypatch) == []


def test_doctor_recusa_carrossel_sem_assets(tmp_path, monkeypatch):
    p = problemas_com(tmp_path, {"kind": "carousel"}, monkeypatch)
    assert any("sem media.assets" in x for x in p), p


def test_doctor_recusa_carrossel_com_um_slide(tmp_path, monkeypatch):
    media = {"kind": "carousel", "assets": [{"asset_url": "a"}]}
    p = problemas_com(tmp_path, media, monkeypatch)
    assert any("1 slides" in x for x in p), p


def test_doctor_recusa_carrossel_com_onze_slides(tmp_path, monkeypatch):
    media = {"kind": "carousel", "assets": [{"asset_url": str(i)} for i in range(11)]}
    p = problemas_com(tmp_path, media, monkeypatch)
    assert any("11 slides" in x and "2 a 10" in x for x in p), p


def test_doctor_recusa_slide_sem_url(tmp_path, monkeypatch):
    media = {"kind": "carousel", "assets": [{"asset_url": "a"}, {}]}
    p = problemas_com(tmp_path, media, monkeypatch)
    assert any("slide sem asset_url" in x for x in p), p


def test_doctor_ainda_exige_asset_url_de_imagem(tmp_path, monkeypatch):
    p = problemas_com(tmp_path, {"kind": "image"}, monkeypatch)
    assert any("sem asset_url" in x for x in p), p


def test_doctor_aceita_imagem_com_asset_url(tmp_path, monkeypatch):
    assert problemas_com(tmp_path, {"kind": "image", "asset_url": "a"}, monkeypatch) == []


def test_doctor_ainda_exige_asset_url_de_reel(tmp_path, monkeypatch):
    """Item antigo, sem kind: a regra do Reel nao pode ter afrouxado."""
    p = problemas_com(tmp_path, {}, monkeypatch)
    assert any("sem asset_url" in x for x in p), p
