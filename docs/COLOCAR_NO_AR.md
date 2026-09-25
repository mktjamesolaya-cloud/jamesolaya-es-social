# Colocar o @jamesolaya.es no ar

Estado em 25/09/2026. O que está pronto, o que falta, e em que ordem.

## O que já está de pé

| | |
|---|---|
| Projeto | Cópia do publicador do @_lukasmax, sem `data/` nem `media/` e sem nenhuma credencial. Pacote renomeado para `jayes_automation`, comando `jayes`. |
| Testes | 245 herdados + 12 novos da mescla, todos passando. |
| Horários | `data/slots.json` — 2 posts/dia, 12:00 e 18:00, fuso `America/Mexico_City`, jitter de ±20 min. |
| Mescla de formatos | `src/jayes_automation/mescla.py`. Intercala vídeo e estático para o feed não virar bloco do mesmo tipo. |
| Validador | `scripts/validar_conta.py` — confere token, conta, permissão e cota sem publicar nada. |
| Guia da conta | `docs/META_APP_SETUP.md`, adaptado para @jamesolaya.es, incluindo a decisão Empresa × Criador. |

## O que falta, na ordem

### 1. Você: criar o app na Meta e gerar o token

Siga `docs/META_APP_SETUP.md`. São uns 15 minutos. No fim você tem duas coisas: `INSTAGRAM_USER_ID` e `INSTAGRAM_ACCESS_TOKEN`.

Três armadilhas que o guia detalha e que respondem por quase toda tentativa travada: o app tem que ser do tipo **Empresa** (senão o card do Instagram nem aparece), a conta precisa estar **pública**, e o pop-up de login **reusa a sessão do navegador sem avisar** — faça em janela anônima com só a conta certa logada.

Depois:

```bash
cp .env.example .env     # preencha USER_ID e ACCESS_TOKEN
uv run python scripts/validar_conta.py
```

Ele diz, linha a linha, se o token abre a conta certa, se a permissão de publicar está ativa e quanto resta da cota de 24 horas. Enquanto isso não passar, o resto não adianta.

### 2. ~~Eu: estender o publicador para imagem e carrossel~~ — FEITO

`instagram.py` ganhou três métodos ao lado do de Reels:

- `create_image_container` — foto única. Note que ele **não manda `media_type`**: a ausência dele é o que identifica imagem na API. Só aceita JPEG.
- `create_carousel_item` — um slide. Nunca leva legenda; quem carrega é o pai.
- `create_carousel_container` — amarra os slides. Recusa fora da faixa de 2 a 10 **antes de chamar a API**, porque a essa altura os filhos já custaram cota.

`publisher.py` ganhou `media_kind()` e despacho por tipo. Item sem `kind` é tratado como `reel`, então a fila antiga continua funcionando.

**O cuidado com os filhos órfãos.** Um carrossel são N+1 contêineres. Se o processo morrer entre o último slide e o pai, os slides viram órfãos: já consumiram cota e não dá para apagar. Por isso cada id de filho é gravado na fila e persistido assim que criado — uma reexecução reaproveita os filhos em vez de criar outra leva. Tem teste para exatamente esse cenário.

`doctor` também aprendeu carrossel: antes ele exigia `asset_url` de todo item e reprovaria todo carrossel válido, já que carrossel guarda os assets em `media.assets`. Agora valida slide a slide, e com `--check-assets` confere cada um.

**Verificação:** 289 testes passando (eram 245 herdados), lint limpo. Os testes do `doctor` chamam o CLI de verdade — confirmei por mutação que, desfazendo a correção, 5 deles falham.

### 3. Eu: ingestão e legendas

Trocar a ingestão do TikTok por uma que lê `melhores-conteudos/` e a triagem. Depois as legendas em espanhol — adaptadas, não traduzidas — com aprovação sua antes de entrar na fila, como no projeto irmão.

### 4. Você: repositório no GitHub e secrets

O cron roda no GitHub Actions e a mídia é hospedada como asset de Release **deste** repositório — é assim que a Meta recebe uma URL pública. Então o repo precisa existir antes de publicar.

- Secrets: `INSTAGRAM_USER_ID`, `INSTAGRAM_ACCESS_TOKEN`, `SECRETS_PAT`
- Variables: `PUBLISH_ENABLED` = `false` (por enquanto), `INSTAGRAM_API_VERSION` = `v26.0`

O `SECRETS_PAT` é um token fine-grained com `Secrets: Read and write`. Ele existe para o cron mensal renovar o token do Instagram sozinho. **Sem ele o sistema para de publicar em 60 dias, sem avisar.**

### 5. Os dois: um post de teste, observado

Com `PUBLISH_ENABLED=false`, rodar `jayes doctor --check-assets` e depois `publish-due --dry-run`. Só então um post real, disparado à mão, conferido no app. Ligar o cron por último.

## A fila que existe hoje

**326 posts** com arquivo em disco e triados como baratos: **98 vídeos e 228 estáticos** (129 fotos, 99 carrosséis). Vieram de 586 posts baixados no total, 2,6 GB.

A proporção de vídeo é um botão em `mescla.ALVO_VIDEO_PADRAO`, hoje em `0.30`. Onde cada escolha chega:

| Vídeo no feed | Cobre | Acaba primeiro |
|---|---|---|
| 50% | 98 dias (01/01/2027) | vídeo |
| 40% | 122 dias (25/01/2027) | vídeo |
| **30%** | **163 dias (07/03/2027)** | empatam |

Todas passam de 31/12. `0.30` é o ponto onde os dois estoques acabam no mesmo dia — nada fica órfão. Se quiser o feed mais pesado em vídeo, `0.40` ainda sobra dois meses.

### De onde veio esse acervo

Três lotes, todos com o critério **índice ≥ 1,00** — só post que bateu a mediana da própria época:

- os 196 melhores de todos os formatos (o lote original);
- +180 Reels, para o vídeo deixar de ser o gargalo;
- +210 fotos e carrosséis, porque com os vídeos novos o gargalo virou o estático.

Usei índice e não score porque score embute peso de recência. Para uma conta nova em espanhol, que nunca viu nada disso, "foi bom quando saiu" vale mais que "é recente".

### Ainda dá para esticar

**123 posts no balde C médio** — precisam de alguém refazendo a arte em espanhol. É design, não código.

## Dois riscos que não são de código

**Música embutida.** 54 dos 98 vídeos (55%) têm música estrangeira dentro do arquivo. Republicar via API pode render mute ou bloqueio por direitos autorais, porque o áudio viaja dentro do MP4 e a proteção da biblioteca do Instagram não vale. Vale trocar a trilha antes de subir. Os 228 estáticos não têm esse risco — e sozinhos já cobrem 114 dias a 2 por dia.

**Direito de imagem.** O material mostra cliente real, incluindo alopecia e pós-quimio. A autorização original dificilmente previa republicação em outro perfil, em outro idioma, para outro público. Isso precisa estar resolvido antes do primeiro post, e não é o pipeline que resolve.

## Formato do item na fila

```jsonc
// Reel — como sempre foi. Item sem "kind" também cai aqui.
{ "media": { "kind": "reel", "asset_url": "https://…/v.mp4", "thumb_offset_ms": 1500 } }

// Foto única
{ "media": { "kind": "image", "asset_url": "https://…/f.jpg", "alt_text": "…" } }

// Carrossel — 2 a 10 slides, na ordem em que o usuário desliza
{ "media": { "kind": "carousel", "assets": [
    { "asset_url": "https://…/1.jpg" },
    { "asset_url": "https://…/2.jpg" },
    { "asset_url": "https://…/3.mp4", "is_video": true }
] } }
```

Duas regras da Meta que mordem no carrossel: **máximo 10 itens**, e **todos os slides são cortados pela proporção do primeiro** — um slide 9:16 depois de um 1:1 perde topo e base sem aviso. Padronizar a proporção é trabalho da preparação, antes de hospedar.
