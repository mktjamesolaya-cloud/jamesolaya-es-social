"""Contrato dos containers de imagem e carrossel.

Nao chamam a rede: substituem ``_post`` e conferem exatamente quais campos
saem. Sao esses campos que a Meta valida, e errar um so aparece em producao.
"""

import pytest

from jayes_automation.instagram import InstagramPublisher, PermanentError


class Espiao(InstagramPublisher):
    """Guarda as chamadas em vez de falar com a API."""

    def __init__(self):
        super().__init__("42", "token-de-teste")
        self.chamadas: list[tuple[str, dict]] = []
        self.resposta = {"id": "container-1"}

    def _post(self, path, **fields):  # type: ignore[override]
        self.chamadas.append((path, fields))
        return self.resposta


@pytest.fixture
def ig():
    return Espiao()


# --- imagem unica ---------------------------------------------------------

def test_imagem_nao_manda_media_type(ig):
    """A ausencia de media_type e o que identifica imagem. Mandar IMAGE nao e documentado."""
    ig.create_image_container("https://x/y.jpg", "hola")
    _, campos = ig.chamadas[0]
    assert "media_type" not in campos
    assert campos["image_url"] == "https://x/y.jpg"
    assert campos["caption"] == "hola"


def test_imagem_vai_para_o_endpoint_de_media_do_usuario(ig):
    ig.create_image_container("https://x/y.jpg", "hola")
    assert ig.chamadas[0][0] == "42/media"


def test_imagem_leva_alt_text_quando_informado(ig):
    ig.create_image_container("https://x/y.jpg", "hola", alt_text="ceja reconstruida")
    assert ig.chamadas[0][1]["alt_text"] == "ceja reconstruida"


def test_imagem_omite_alt_text_vazio(ig):
    """urlencode transformaria None no literal 'None' e a Meta rejeita."""
    ig.create_image_container("https://x/y.jpg", "hola")
    assert "alt_text" not in ig.chamadas[0][1]


def test_imagem_como_slide_nao_leva_legenda(ig):
    """Quem carrega a legenda do carrossel e o pai, nunca o slide."""
    ig.create_image_container("https://x/y.jpg", "hola", is_carousel_item=True)
    campos = ig.chamadas[0][1]
    assert campos["is_carousel_item"] == "true"
    assert "caption" not in campos
    assert "alt_text" not in campos


# --- slides ---------------------------------------------------------------

def test_slide_de_imagem(ig):
    ig.create_carousel_item("https://x/1.jpg")
    campos = ig.chamadas[0][1]
    assert campos == {"is_carousel_item": "true", "image_url": "https://x/1.jpg"}


def test_slide_de_video_usa_media_type_video_e_nao_reels(ig):
    """Dentro de carrossel o tipo e VIDEO. REELS ali dentro nao existe."""
    ig.create_carousel_item("https://x/1.mp4", is_video=True)
    campos = ig.chamadas[0][1]
    assert campos["media_type"] == "VIDEO"
    assert campos["video_url"] == "https://x/1.mp4"
    assert "image_url" not in campos


# --- container pai --------------------------------------------------------

def test_carrossel_manda_children_separados_por_virgula_na_ordem(ig):
    ig.create_carousel_container(["a", "b", "c"], "hola")
    campos = ig.chamadas[0][1]
    assert campos["media_type"] == "CAROUSEL"
    assert campos["children"] == "a,b,c"
    assert campos["caption"] == "hola"


def test_carrossel_recusa_mais_de_dez_itens_antes_de_chamar_a_api(ig):
    """Falhar cedo importa: os filhos ja custaram cota quando o pai falharia."""
    with pytest.raises(PermanentError, match="2 a 10"):
        ig.create_carousel_container([str(i) for i in range(11)], "hola")
    assert ig.chamadas == []


def test_carrossel_recusa_um_item_so(ig):
    with pytest.raises(PermanentError, match="2 a 10"):
        ig.create_carousel_container(["a"], "hola")
    assert ig.chamadas == []


def test_carrossel_aceita_os_extremos(ig):
    ig.create_carousel_container(["a", "b"], "hola")
    ig.create_carousel_container([str(i) for i in range(10)], "hola")
    assert len(ig.chamadas) == 2


def test_carrossel_aceita_qualquer_iteravel(ig):
    ig.create_carousel_container((f"c{i}" for i in range(3)), "hola")
    assert ig.chamadas[0][1]["children"] == "c0,c1,c2"


# --- o Reel nao pode ter mudado -------------------------------------------

def test_reel_continua_igual(ig):
    ig.create_container_from_url("https://x/v.mp4", "hola", thumb_offset_ms=1500)
    campos = ig.chamadas[0][1]
    assert campos["media_type"] == "REELS"
    assert campos["video_url"] == "https://x/v.mp4"
    assert campos["thumb_offset"] == "1500"
