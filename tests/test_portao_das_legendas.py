"""O caminho entre uma legenda escrita e uma legenda publicada.

Este arquivo nasce de um buraco silencioso: ``Paths.captions_dir`` apontava
para ``data/captions`` (heranca do projeto irmao) enquanto as legendas em
espanhol viviam em ``data/captions-es``. O efeito nao foi um erro, foi um
vazio -- ``review-captions`` e ``approve-caption`` olhavam uma pasta que nao
existia e nao diziam nada. As quatro primeiras legendas do perfil foram
escritas direto no disco, foram ao ar com ``status: draft`` e ``approved_at:
null``, e so passaram no validador por sorte, nunca por processo.

O que estes testes seguram: onde as legendas moram, e que rascunho nao entra
na fila.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from jayes_automation import captions_es
from jayes_automation.paths import Paths

RAIZ = Path(__file__).resolve().parents[1]
PASTA_REAL = RAIZ / "data" / "captions-es"


def registro(short_code: str, *, caption: str, status: str = "draft") -> dict:
    return {
        "tiktok_id": short_code,
        "caption": caption,
        "hashtags": ["#cejas", "#micropigmentacion", "#peloapelo"],
        "status": status,
        "approved_at": None,
        "warnings": [],
    }


LEGENDA_OK = (
    "Esto no empieza con una tecnica, empieza con una conversacion honesta "
    "sobre lo que la clienta realmente quiere ver en el espejo cada manana."
)


# --- onde as legendas moram ------------------------------------------------


def test_captions_dir_aponta_para_a_pasta_em_espanhol():
    assert Paths.resolve(RAIZ).captions_dir.name == "captions-es"


def test_o_cli_enxerga_as_legendas_que_existem_no_disco():
    """A guarda central: a pasta do codigo e a pasta dos arquivos sao a mesma.

    Se alguem trocar ``captions_dir`` de volta, este teste fica vermelho em vez
    de o projeto voltar a nao achar nada em silencio.
    """
    do_codigo = Paths.resolve(RAIZ).captions_dir
    assert do_codigo == PASTA_REAL
    assert sorted(p.name for p in do_codigo.glob("*.json")) == sorted(
        p.name for p in PASTA_REAL.glob("*.json")
    )
    assert list(do_codigo.glob("*.json")), "nenhuma legenda encontrada"


# --- o portao --------------------------------------------------------------


def test_rascunho_nao_e_carregado_para_a_fila(tmp_path):
    pasta = tmp_path / "captions-es"
    pasta.mkdir()
    (pasta / "A.json").write_text(
        json.dumps(registro("A", caption=LEGENDA_OK, status="draft")), encoding="utf-8"
    )
    (pasta / "B.json").write_text(
        json.dumps(registro("B", caption=LEGENDA_OK, status="approved")), encoding="utf-8"
    )
    aprovadas = captions_es.carregar_aprovadas(pasta)
    assert set(aprovadas) == {"B"}


def test_pasta_inexistente_devolve_vazio_em_vez_de_explodir(tmp_path):
    assert captions_es.carregar_aprovadas(tmp_path / "nao-existe") == {}


def test_a_legenda_carregada_ja_traz_as_hashtags(tmp_path):
    """E o texto completo que vai para a API, nao so o corpo."""
    pasta = tmp_path / "captions-es"
    pasta.mkdir()
    (pasta / "A.json").write_text(
        json.dumps(registro("A", caption=LEGENDA_OK, status="approved")), encoding="utf-8"
    )
    texto = captions_es.carregar_aprovadas(pasta)["A"]
    assert texto.startswith(LEGENDA_OK)
    assert "#micropigmentacion" in texto


def test_aprovar_recusa_legenda_com_aviso():
    r = registro("A", caption="corta")  # abaixo do minimo de caracteres
    with pytest.raises(captions_es.LegendaError):
        captions_es.aprovar(r)
    assert r["status"] == "draft"
    assert r["warnings"], "o registro deveria guardar o motivo da recusa"


def test_aprovar_carimba_a_data():
    r = registro("A", caption=LEGENDA_OK)
    captions_es.aprovar(r)
    assert r["status"] == "approved"
    assert r["approved_at"]


def test_forcar_passa_por_cima_mas_guarda_o_aviso():
    r = registro("A", caption="corta")
    captions_es.aprovar(r, forcar=True)
    assert r["status"] == "approved"
    assert r["warnings"], "aprovar a forca nao pode apagar o aviso"


# --- as legendas de verdade ------------------------------------------------


@pytest.mark.parametrize("caminho", sorted(PASTA_REAL.glob("*.json")), ids=lambda p: p.stem)
def test_legenda_no_disco_passa_no_validador(caminho: Path):
    r = json.loads(caminho.read_text(encoding="utf-8"))
    avisos = captions_es.validate_es(r.get("caption") or "", r.get("hashtags") or [])
    assert not avisos, f"{caminho.stem}: {avisos}"


@pytest.mark.parametrize("caminho", sorted(PASTA_REAL.glob("*.json")), ids=lambda p: p.stem)
def test_legenda_no_disco_esta_aprovada(caminho: Path):
    """Aprovacao e um fato no disco, nao uma lembranca da conversa."""
    r = json.loads(caminho.read_text(encoding="utf-8"))
    assert r.get("status") == "approved", f"{caminho.stem} esta como {r.get('status')}"
    assert r.get("approved_at"), f"{caminho.stem} sem approved_at"
