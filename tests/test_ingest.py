"""Ingestao da pasta de midia.

Monta uma arvore sintetica em tmp_path: e o unico jeito de testar os casos
que quebram (pasta faltando, carrossel sem slides, slide 11) sem depender do
acervo real, que muda.
"""

import json

import pytest

from jayes_automation import ingest


def monta(raiz, pasta, *, arquivos=(), legenda="legenda original"):
    d = raiz / pasta
    d.mkdir(parents=True, exist_ok=True)
    for nome in arquivos:
        (d / nome).write_bytes(b"x" * 100)
    if legenda is not None:
        (d / "legenda.txt").write_text(legenda, encoding="utf-8")
    return d


def escreve_dados(dados, triagem, manifesto=None):
    dados.mkdir(parents=True, exist_ok=True)
    (dados / "triagem-es.json").write_text(json.dumps(triagem), encoding="utf-8")
    (dados / "manifesto-download.json").write_text(
        json.dumps({"itens": manifesto or []}), encoding="utf-8"
    )


def t(pasta, formato, *, balde="A", esforco=None, posicao=1, score=10.0):
    return {
        "posicao": posicao,
        "pasta": pasta,
        "formato": formato,
        "balde": balde,
        "esforco": esforco,
        "score": score,
        "data": "2026-01-01",
        "url": "https://www.instagram.com/p/ABC123/",
        "editorial": "Aula técnica",
    }


@pytest.fixture
def arvore(tmp_path):
    midia = tmp_path / "melhores-conteudos"
    dados = tmp_path / "data"
    return midia, dados


# --- tipos ----------------------------------------------------------------


def test_reel_pega_o_video_e_a_capa(arvore):
    midia, dados = arvore
    monta(midia, "001_Reel_x", arquivos=["video.mp4", "capa.jpg"])
    escreve_dados(dados, [t("001_Reel_x", "Reel")])
    c = ingest.carregar(midia, dados)[0]
    assert c.media_kind == "reel"
    assert [p.name for p in c.arquivos] == ["video.mp4"]
    assert c.capa.name == "capa.jpg"


def test_imagem_unica(arvore):
    midia, dados = arvore
    monta(midia, "002_Img", arquivos=["imagem.jpg"])
    escreve_dados(dados, [t("002_Img", "Imagem única")])
    c = ingest.carregar(midia, dados)[0]
    assert c.media_kind == "image"
    assert [p.name for p in c.arquivos] == ["imagem.jpg"]


def test_carrossel_pega_os_slides_em_ordem(arvore):
    midia, dados = arvore
    monta(midia, "003_Car", arquivos=[f"slide-{i:02d}.jpg" for i in (3, 1, 2)] + ["imagem.jpg"])
    escreve_dados(dados, [t("003_Car", "Carrossel")])
    c = ingest.carregar(midia, dados)[0]
    assert c.media_kind == "carousel"
    assert [p.name for p in c.arquivos] == ["slide-01.jpg", "slide-02.jpg", "slide-03.jpg"]


def test_carrossel_corta_em_dez_slides(arvore):
    """A Meta rejeita acima de 10. Cortar e melhor que perder o post."""
    midia, dados = arvore
    monta(midia, "004_Car", arquivos=[f"slide-{i:02d}.jpg" for i in range(1, 15)])
    escreve_dados(dados, [t("004_Car", "Carrossel")])
    c = ingest.carregar(midia, dados)[0]
    assert c.slides == 10
    assert c.arquivos[-1].name == "slide-10.jpg"


def test_carrossel_com_um_slide_vira_imagem(arvore):
    """Um slide so nao e carrossel para a API -- ela exige 2 a 10."""
    midia, dados = arvore
    monta(midia, "005_Car", arquivos=["slide-01.jpg"])
    escreve_dados(dados, [t("005_Car", "Carrossel")])
    c = ingest.carregar(midia, dados)[0]
    assert c.media_kind == "image"


def test_carrossel_sem_slides_cai_para_a_imagem_principal(arvore):
    midia, dados = arvore
    monta(midia, "006_Car", arquivos=["imagem.jpg"])
    escreve_dados(dados, [t("006_Car", "Carrossel")])
    c = ingest.carregar(midia, dados)[0]
    assert c.media_kind == "image"
    assert [p.name for p in c.arquivos] == ["imagem.jpg"]


# --- descartes ------------------------------------------------------------


def test_descarta_balde_d(arvore):
    midia, dados = arvore
    monta(midia, "007_x", arquivos=["imagem.jpg"])
    escreve_dados(dados, [t("007_x", "Imagem única", balde="D")])
    assert ingest.carregar(midia, dados) == []


def test_descarta_c_medio_mas_aceita_c_leve(arvore):
    midia, dados = arvore
    monta(midia, "008_leve", arquivos=["imagem.jpg"])
    monta(midia, "009_medio", arquivos=["imagem.jpg"])
    escreve_dados(
        dados,
        [
            t("008_leve", "Imagem única", balde="C", esforco="leve", posicao=8),
            t("009_medio", "Imagem única", balde="C", esforco="médio", posicao=9),
        ],
    )
    pastas = [c.pasta for c in ingest.carregar(midia, dados)]
    assert pastas == ["008_leve"]


def test_corte_configuravel_amplia_a_fila(arvore):
    midia, dados = arvore
    monta(midia, "010_medio", arquivos=["imagem.jpg"])
    escreve_dados(dados, [t("010_medio", "Imagem única", balde="C", esforco="médio")])
    assert ingest.carregar(midia, dados) == []
    ampliado = ingest.carregar(
        midia, dados, baldes=frozenset({"A", "B", "C"}), esforcos=frozenset({"leve", "médio"})
    )
    assert len(ampliado) == 1


def test_descarta_pasta_que_nao_existe_no_disco(arvore):
    midia, dados = arvore
    midia.mkdir(parents=True)
    escreve_dados(dados, [t("999_fantasma", "Imagem única")])
    assert ingest.carregar(midia, dados) == []
    assert "pasta ausente no disco" in ingest.carregar.ultimo_descarte


def test_descarta_pasta_sem_arquivo_de_midia(arvore):
    midia, dados = arvore
    monta(midia, "011_vazia", arquivos=[])
    escreve_dados(dados, [t("011_vazia", "Reel")])
    assert ingest.carregar(midia, dados) == []


# --- ordem e metadados ----------------------------------------------------


def test_ordena_do_melhor_para_o_pior(arvore):
    midia, dados = arvore
    for i in (1, 2, 3):
        monta(midia, f"{i:03d}_x", arquivos=["imagem.jpg"])
    escreve_dados(
        dados,
        [
            t("001_x", "Imagem única", posicao=1, score=1.0),
            t("002_x", "Imagem única", posicao=2, score=50.0),
            t("003_x", "Imagem única", posicao=3, score=10.0),
        ],
    )
    assert [c.score for c in ingest.carregar(midia, dados)] == [50.0, 10.0, 1.0]


def test_le_a_legenda_original(arvore):
    midia, dados = arvore
    monta(midia, "012_x", arquivos=["imagem.jpg"], legenda="Olha esse resultado")
    escreve_dados(dados, [t("012_x", "Imagem única")])
    assert ingest.carregar(midia, dados)[0].legenda_original == "Olha esse resultado"


def test_extrai_o_shortcode_da_url(arvore):
    midia, dados = arvore
    monta(midia, "013_x", arquivos=["imagem.jpg"])
    escreve_dados(dados, [t("013_x", "Imagem única")])
    assert ingest.carregar(midia, dados)[0].short_code == "ABC123"


def test_falha_alto_sem_triagem(arvore):
    midia, dados = arvore
    midia.mkdir(parents=True)
    dados.mkdir(parents=True)
    with pytest.raises(ingest.IngestError, match="Triagem ausente"):
        ingest.carregar(midia, dados)


def test_falha_alto_sem_manifesto(arvore):
    midia, dados = arvore
    midia.mkdir(parents=True)
    dados.mkdir(parents=True)
    (dados / "triagem-es.json").write_text("[]", encoding="utf-8")
    with pytest.raises(ingest.IngestError, match="manifesto"):
        ingest.carregar(midia, dados)


def test_resumir_conta_arquivos_e_tipos(arvore):
    midia, dados = arvore
    monta(midia, "014_r", arquivos=["video.mp4"])
    monta(midia, "015_c", arquivos=["slide-01.jpg", "slide-02.jpg", "slide-03.jpg"])
    escreve_dados(
        dados,
        [
            t("014_r", "Reel", posicao=14),
            t("015_c", "Carrossel", posicao=15),
        ],
    )
    r = ingest.resumir(ingest.carregar(midia, dados))
    assert r["candidatos"] == 2
    assert r["por_tipo"] == {"reel": 1, "carousel": 1}
    assert r["arquivos_a_hospedar"] == 4


def test_veto_manual_tira_o_post_da_fila(arvore):
    """A triagem aprova pelo que ve na midia; o veto existe para o que ela nao julga."""
    midia, dados = arvore
    monta(midia, "020_anitta", arquivos=["imagem.jpg"])
    escreve_dados(dados, [t("020_anitta", "Imagem única", score=999.0)])
    assert len(ingest.carregar(midia, dados)) == 1

    (dados / "excluidos-es.json").write_text(
        json.dumps({"excluidos": {"ABC123": "fofoca de pop brasileiro"}}), encoding="utf-8"
    )
    assert ingest.carregar(midia, dados) == []
    assert "veto manual" in ingest.carregar.ultimo_descarte


def test_veto_pode_viver_em_outro_diretorio(tmp_path):
    """A triagem vem do projeto de analise; o veto e decisao deste projeto."""
    midia = tmp_path / "midia"
    dados = tmp_path / "analise"
    vetos = tmp_path / "projeto"
    vetos.mkdir()
    monta(midia, "030_x", arquivos=["imagem.jpg"])
    escreve_dados(dados, [t("030_x", "Imagem única")])
    (vetos / "excluidos-es.json").write_text(
        json.dumps({"excluidos": {"ABC123": "motivo"}}), encoding="utf-8"
    )
    assert len(ingest.carregar(midia, dados)) == 1, "sem dir_vetos, nada e vetado"
    assert ingest.carregar(midia, dados, dir_vetos=vetos) == []
