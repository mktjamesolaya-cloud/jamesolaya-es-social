# Rotina automática de legendas

Procedimento que um agente segue a cada hora para manter a fila do
@jamesolaya.es cheia sem pedir aprovação a cada lote.

Decisão do cliente em 28/09/2026: *"não precisa ficar aprovando"*. O que essa
decisão custa está escrito aqui embaixo, na seção **Pule sem pensar duas
vezes** — a aprovação humana era a rede que pegou a sobrancelha masculina
descrita como feminina, o meme da Cardi B no último slide, o nome próprio
traduzido e o card "AGENDE SUA AVALIAÇÃO". Sem ela, a regra é **pular na
dúvida**, nunca arriscar.

Este arquivo é a fonte da verdade da rotina. O prompt do cron só aponta para
cá, de propósito: assim o procedimento sobrevive a uma compactação de contexto
e o cliente pode ler e editar o que o robô faz.

---

## A cada disparo

### 1. Sair cedo (o caso comum)

A fila consome 2 posts por dia. A rotina só trabalha quando ela encurta. Na
maioria das horas o disparo termina aqui, custando um `git pull` e a leitura de
um JSON.

```bash
cd /Users/lucasferreira/PROJETOS_DEV/jamesolaya-es-social
git pull --ff-only origin main
```

**Pare e não faça nada** se qualquer uma for verdade:

| Condição | Por quê |
|---|---|
| Último `scheduled_at` está a mais de **6 dias** | já tem fila de sobra |
| Há item em `publishing` | pode haver publicação a meio caminho |
| `git status` sujo | alguém está mexendo à mão |
| `jayes doctor` reprova | resolver o problema vem antes de acrescentar |
| Menos de 10 candidatos sem legenda | avise o cliente, não trabalhe |

### 2. Escolher 2 candidatos

Dois por vez, não oito: distribui o custo ao longo do dia e limita o estrago de
um erro de julgamento a dois posts.

```bash
uv run python - <<'PY'
from pathlib import Path
from jayes_automation import ingest, celebridades, captions_es
cs = ingest.carregar(
    Path.home()/"PROJETOS_DEV/FormatosValidadosJamesolaya/melhores-conteudos",
    Path.home()/"PROJETOS_DEV/FormatosValidadosJamesolaya/data",
    dir_vetos=Path("data"), dir_adaptado=Path("media/adaptado-es"))
feitas = captions_es.carregar_aprovadas(Path("data/captions-es"))
# ... filtrar, ordenar por score, pegar os 2 primeiros
PY
```

Critérios, nesta ordem:

- **Só balde A.** Balde C tem português queimado na imagem e editar é uma
  decisão, não uma tarefa — fica para o cliente pedir.
- Sem legenda aprovada ainda, e fora da fila.
- `celebridades.suspeito()` falso sobre legenda + texto na tela + transcrição.
- Maior score primeiro.
- **Não repetir tema.** Compare com as últimas 6 legendas aprovadas: se o
  roteiro for quase o mesmo ("não é falta de prática, é falta de método"
  apareceu em quatro posts diferentes), pule e pegue o próximo.

### 3. Abrir a mídia. Sempre.

Esta é a etapa que não tem atalho e é a razão de a rotina existir em vez de um
script. O OCR e a transcrição são apoio, **nunca substituto**: o meme da Cardi B
não deixou nenhum rastro no texto, e o card em português do `DULJ5ghDq5X` estava
num post que a triagem marcou como limpo.

- Imagem e carrossel: ler cada arquivo.
- Reel: extrair frames em 2%, 15%, 35%, 55%, 75% e 95% da duração e ler todos.
- Rodar OCR ao longo do vídeo inteiro, não só na capa:
  `tools/ocr` do projeto de análise, a cada 5 s.
- Ler a legenda original inteira.

### 4. Pule sem pensar duas vezes

Registre em `data/pulados-es.json` com o motivo e siga para o próximo. Pular é
barato; um post errado no ar não é.

- **Qualquer pessoa que aparente ser menor de idade.**
- Qualquer figura pública, mesmo só na imagem, mesmo em meme.
- Português queimado em qualquer frame.
- Amarra com o Brasil que a legenda não resolve: turma presencial datada,
  cidade, telefone, R$, "link na bio" para oferta local.
- Condição médica visível cuja legenda original não explica o contexto.
- **Qualquer coisa que você não entendeu olhando.** Se precisa supor o que o
  post mostra, pule.

### 5. Escrever

Regras que já custaram erro:

- **Espanhol neutro, tuteo.** `mira`/`sientes`, nunca `mirá`/`sentís`. México é
  o maior mercado e o público hispano dos EUA é majoritariamente de origem
  mexicana; nenhum dos dois usa voseo.
- **Adaptar, não traduzir.** O CTA brasileiro (agendar na clínica de Campinas,
  turma presencial) vira o curso online ou nada.
- **Nome próprio e nome de produto não se traduzem.** "Heitor" não vira
  "Héctor"; "Escuro de Hades" é SKU e fica como está.
- A legenda tem que descrever o que está **na imagem**. Se o post é uma
  sobrancelha masculina, não escreva "de ellas".
- Não prometa o que a conta não entrega: nada de "amanhã eu respondo".
- 400 a 800 caracteres, 3 a 8 hashtags em espanhol.
- Os primeiros 125 caracteres não podem cortar no meio de uma frase — é o que
  aparece antes do "mais".

### 6. Gravar, agendar, conferir

```bash
uv run jayes importar-legendas-es --file <lote.json> --aprovar
uv run jayes plan-es --quantidade 2
uv run jayes doctor --check-assets
uv run pytest -q > /tmp/pt.txt 2>&1; echo "EXIT=$?"
uv run ruff check src tests && uv run ruff format --check src tests
```

`importar-legendas-es --aprovar` só grava como `approved` o que passar no
validador. Se bloquear, **conserte a legenda** — nunca use `--forcar`.

Antes de hospedar, confira a conta do `gh`:

```bash
gh api user -q .login   # tem que dizer mktjamesolaya-cloud
```

A máquina tem três contas logadas e `gh auth switch` é global: outra sessão
pode trocar a ativa no meio do trabalho. Já aconteceu duas vezes em 28/09.

### 7. Commitar e registrar

Só commite com testes verdes e `doctor` ok. Nunca `--force`.

Acrescente uma linha a `data/rotina-legendas.json`: data, ids acrescentados,
ids pulados com o motivo. É por aí que o cliente audita depois sem precisar ler
o histórico do git.

---

## Limites

- **Nunca publica.** A rotina só enche a fila; quem publica é o cron do
  GitHub Actions, com as mesmas três travas anti-duplicação de sempre.
- **Nunca edita mídia.** Reeditar vídeo é decisão do cliente, via
  `data/adaptacoes-es.json`.
- **Nunca mexe em item já agendado ou publicado.**
- **Nunca usa `--forcar` em legenda.**
- Se algo parecer errado e não houver regra aqui: pare e escreva para o
  cliente em vez de decidir sozinho.

## Para o cliente

Ver o que entrou:

```bash
uv run jayes review-captions --status approved
cat data/rotina-legendas.json
```

Tirar algo da fila antes de publicar: `uv run jayes bump --id <id>` ou editar
`data/queue.json` e commitar.

Vetar um post para sempre: acrescente em `data/excluidos-es.json`.
