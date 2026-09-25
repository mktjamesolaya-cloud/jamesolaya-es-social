#!/usr/bin/env python3
"""Confere se as credenciais de @jamesolaya.es estao prontas para publicar.

Roda ANTES de existir qualquer fila, que e o momento em que o `doctor` ainda
nao serve. Nao publica nada e nao escreve nada -- so pergunta a API.

    uv run python scripts/validar_conta.py

Le INSTAGRAM_USER_ID e INSTAGRAM_ACCESS_TOKEN do .env (ou do ambiente).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "src"))

from jayes_automation.instagram import InstagramPublisher, InstagramError  # noqa: E402

CONTA_ESPERADA = "jamesolaya.es"

OK, FALHA, AVISO = "  OK  ", " FALHA", " AVISO"


def _carregar_env() -> None:
    env = RAIZ / ".env"
    if not env.exists():
        return
    for linha in env.read_text(encoding="utf-8").splitlines():
        linha = linha.strip()
        if not linha or linha.startswith("#") or "=" not in linha:
            continue
        chave, _, valor = linha.partition("=")
        os.environ.setdefault(chave.strip(), valor.strip().strip("\"'"))


def main() -> int:
    _carregar_env()
    user_id = os.environ.get("INSTAGRAM_USER_ID", "").strip()
    token = os.environ.get("INSTAGRAM_ACCESS_TOKEN", "").strip()
    problemas = 0

    print(f"\nValidando credenciais de @{CONTA_ESPERADA}\n")

    if not user_id or not token:
        faltando = [
            n for n, v in (("INSTAGRAM_USER_ID", user_id), ("INSTAGRAM_ACCESS_TOKEN", token)) if not v
        ]
        print(f"[{FALHA}] {' e '.join(faltando)} nao definido(s).")
        print("         Copie .env.example para .env e preencha, ou exporte no ambiente.")
        print("         O passo a passo esta em docs/META_APP_SETUP.md")
        return 1

    cliente = InstagramPublisher(user_id, token)

    # 1 — o token abre a conta certa?
    try:
        conta = cliente.check_account()
    except InstagramError as erro:
        print(f"[{FALHA}] O token foi recusado pela API.")
        print(f"         {erro}")
        print("         Provavel: token expirado (valem 60 dias) ou gerado para outra conta.")
        print("         Gere outro em Painel de Apps -> Instagram -> Gerar token.")
        return 1

    username = conta.get("username")
    if username == CONTA_ESPERADA:
        print(f"[{OK}] Token valido e aponta para @{username}")
    else:
        print(f"[{FALHA}] O token aponta para @{username}, nao para @{CONTA_ESPERADA}.")
        print("         Voce logou na conta errada no pop-up. Refaca em janela anonima,")
        print("         com apenas a conta certa logada no Instagram.")
        problemas += 1

    # 2 — o user_id do .env e o que a API devolve (o campo 'id' nao serve)
    devolvido = str(conta.get("user_id") or conta.get("id") or "")
    if devolvido and devolvido != user_id:
        print(f"[{FALHA}] INSTAGRAM_USER_ID ({user_id}) difere do que a API devolve ({devolvido}).")
        print("         Use o campo 'user_id', nao o campo 'id'.")
        problemas += 1
    elif devolvido:
        print(f"[{OK}] INSTAGRAM_USER_ID confere ({user_id})")

    tipo = conta.get("account_type")
    if tipo:
        print(f"[{OK}] Tipo de conta: {tipo}")

    # 3 — a permissao de publicar existe de fato?
    try:
        limite = cliente.content_publishing_limit()
        usado = limite.get("quota_usage", 0)
        total = limite.get("config", {}).get("quota_total", 100)
        print(f"[{OK}] Permissao de publicacao ativa — cota 24h: {usado}/{total} usada")
        if total and usado >= total:
            print(f"[{AVISO}] Cota de 24h esgotada; nada publica ate ela virar.")
    except InstagramError as erro:
        print(f"[{FALHA}] Sem permissao de publicacao (instagram_business_content_publish).")
        print(f"         {erro}")
        print("         Regenere o token aceitando essa permissao na tela de consentimento.")
        problemas += 1

    # 4 — leitura de midia: confirma instagram_business_basic e conta publica
    try:
        recentes = cliente.list_recent_media(limit=3)
        print(f"[{OK}] Leitura de midia funciona ({len(recentes)} post(s) recente(s) visiveis)")
    except InstagramError as erro:
        print(f"[{AVISO}] Nao consegui listar midia recente: {erro}")
        print("         Nao impede publicar, mas o reconcile usa isso como rede de seguranca")
        print("         contra post duplicado. Vale resolver antes de ligar o cron.")

    # 5 — a trava
    if os.environ.get("PUBLISH_ENABLED", "").strip().lower() == "true":
        print(f"[{AVISO}] PUBLISH_ENABLED=true — o cron vai publicar de verdade.")
    else:
        print(f"[{OK}] PUBLISH_ENABLED nao esta em 'true' (trava de seguranca ligada)")

    print()
    if problemas:
        print(f"{problemas} problema(s) a resolver antes de seguir.\n")
        return 1
    print("Credenciais prontas. Proximo passo: montar a fila e publicar 1 post de teste.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
