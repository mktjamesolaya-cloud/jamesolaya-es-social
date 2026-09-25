from jayes_automation import mescla


def item(kind: str, n: int) -> dict:
    return {"media_kind": kind, "id": f"{kind}-{n}"}


def fila(n_video: int, n_estatico: int) -> list[dict]:
    """Ordem de score: todos os videos primeiro, e o pior caso para a mescla."""
    return [item("reel", i) for i in range(n_video)] + [
        item("carousel" if i % 2 else "image", i) for i in range(n_estatico)
    ]


def test_preserva_todos_os_itens():
    entrada = fila(24, 73)
    saida = mescla.intercalar(entrada)
    assert len(saida) == len(entrada)
    assert {i["id"] for i in saida} == {i["id"] for i in entrada}


def test_preserva_a_ordem_de_score_dentro_de_cada_tipo():
    saida = mescla.intercalar(fila(10, 30))
    reels = [i["id"] for i in saida if i["media_kind"] == "reel"]
    assert reels == [f"reel-{i}" for i in range(10)]


def test_quebra_o_bloco_de_video_da_entrada():
    entrada = fila(24, 73)
    assert mescla.resumir(entrada)["maior_sequencia_mesmo_tipo"] == 73
    saida = mescla.intercalar(entrada, alvo_video=0.25)
    # com 1 video a cada 4, a maior sequencia de estatico cai para ~3
    assert mescla.resumir(saida)["maior_sequencia_mesmo_tipo"] <= 4


def test_respeita_a_proporcao_pedida():
    saida = mescla.intercalar(fila(50, 150), alvo_video=0.25)
    prim = saida[:100]
    n_video = sum(1 for i in prim if i["media_kind"] == "reel")
    assert 23 <= n_video <= 27


def test_nao_trava_quando_um_estoque_acaba():
    # so 2 videos para 20 estaticos: depois do 2o video a fila segue normal
    saida = mescla.intercalar(fila(2, 20), alvo_video=0.5)
    assert len(saida) == 22
    assert sum(1 for i in saida if i["media_kind"] == "reel") == 2


def test_alvo_zero_nao_usa_video_ate_acabar_o_estatico():
    saida = mescla.intercalar(fila(5, 5), alvo_video=0.0)
    assert [i["media_kind"] for i in saida[:5]] != ["reel"] * 5


def test_alvo_um_poe_video_primeiro():
    saida = mescla.intercalar(fila(5, 5), alvo_video=1.0)
    assert all(i["media_kind"] == "reel" for i in saida[:5])


def test_entrada_vazia():
    assert mescla.intercalar([]) == []


def test_sugerir_alvo_iguala_a_duracao_dos_dois_estoques():
    # estoque real medido em 25/09/2026
    alvo = mescla.sugerir_alvo(98, 228, posts_por_dia=2)
    assert 0.29 <= alvo <= 0.31
    dias = mescla.duracao_estimada(98, 228, alvo, posts_por_dia=2)
    assert 162 <= dias <= 164


def test_sugerir_alvo_sem_video():
    assert mescla.sugerir_alvo(0, 50) == 0.0


def test_sugerir_alvo_sem_estatico():
    assert mescla.sugerir_alvo(50, 0) == 1.0


def test_duracao_cai_quando_o_alvo_desequilibra():
    equilibrado = mescla.duracao_estimada(98, 228, 0.30)
    demais = mescla.duracao_estimada(98, 228, 0.5)
    assert demais < equilibrado


def test_alvo_maior_ainda_cobre_ate_o_fim_do_ano():
    # 40% de video e uma escolha valida: mais video no feed e ainda passa de 97 dias
    assert mescla.duracao_estimada(98, 228, 0.40) >= 97
