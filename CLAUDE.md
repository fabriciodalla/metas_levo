# CLAUDE.md

Guia para agentes de IA (Claude Code) trabalhando neste repositório. Para o produto e a
arquitetura, comece por [docs/PROJECT.md](docs/PROJECT.md) — este arquivo é só sobre como
trabalhar no código.

## O que é este projeto

App web interno da Levo Alimentos que distribui metas comerciais em KG (inteiro, sem casas
decimais) em cascata por 4 níveis hierárquicos fixos: **Gerente → Coordenador Local →
Supervisor → Vendedor**. A regra mais crítica do sistema: em cada repasse, a
soma distribuída para baixo deve fechar **exatamente** com o recebido de cima — sem sobra, sem
falta. Detalhe em [docs/architecture.md](docs/architecture.md).

## Regra de ouro: não invente as fórmulas

As **5 pendências de cálculo** (sugestão automática por grupo, distribuição Gerente→Local, quebra
grupo→subgrupo, distribuição Supervisor→Vendedor, e o método de arredondamento/rateio de resto) já
foram todas definidas pelo usuário e implementadas — ver
[docs/open-questions.md](docs/open-questions.md) e [docs/decisions.md](docs/decisions.md) (Decisões
6 e 7). **Isso não abre licença para hardcodar fórmula nova sem confirmação**: qualquer pendência
futura (ou revisão de uma fórmula já aprovada) segue exigindo confirmação explícita do usuário antes
de virar requisito definitivo. Continue implementando esses pontos como estratégias plugáveis
(`DistributionStrategy`, `SuggestionStrategy`, `RoundingPolicy`, conforme
[docs/architecture.md](docs/architecture.md)) — fórmula aprovada não significa fórmula travada no
código; se precisar de um placeholder novo para destravar desenvolvimento, marque-o explicitamente
como **não-aprovado** no nome da classe/docstring.

## Stack e estrutura

- **Backend:** Python 3.12, Django 5.2 LTS + Django REST Framework, dentro de `backend/`.
- **Apps de domínio** (`backend/apps/`): `hierarchy` (árvore de nós), `catalog` (grupos/subgrupos/
  produtos), `cycles` (ciclo mensal), `allocations` (`GoalAllocation`, o repasse de meta —
  entidade central), `accounts` (`User` customizado com login próprio e vínculo à hierarquia),
  `audit` (`AuditLogEntry`, histórico de mudanças em hierarquia/catálogo), `sales_history`
  (anticorruption layer para o Postgres externo — sync + `DistributionBaseline`).
- **Frontend:** React + Vite/TS, dentro de `frontend/`, consumindo a API DRF via proxy do Vite
  (sem CORS/porta cruzada) — ver [docs/roadmap.md](docs/roadmap.md), passo 8. Duas telas: grade de
  distribuição (`DistributionPage`/`DistributionForm`, com feedback de soma em tempo real) e a
  tela de gestão do Administrador (`AdminPage`, leitura de hierarquia/catálogo, edição de fato
  continua no Django Admin — Decisão 4).
- **Banco:** PostgreSQL 16, só acessível via Docker Compose.
- Nomenclatura do projeto é **nova**, sem herança de versões anteriores — não existe código
  legado neste repo para seguir de referência.

## Ambiente: tudo roda em Docker

Não existe Python nem Node instalados fora de containers neste projeto — **todo comando roda via
`docker compose exec backend ...` (stack já no ar) ou `docker compose run --rm backend ...`
(stack ainda não subiu)**.

```bash
docker compose up -d                                    # sobe db + backend
docker compose exec backend python manage.py migrate    # aplicar migrações
docker compose exec backend python manage.py makemigrations   # após alterar models
docker compose exec backend pytest                       # testes
docker compose exec backend python manage.py check       # checagem de configuração/models
docker compose exec backend black .                      # formatar
docker compose exec backend ruff check --fix .           # lint + autofix
```

Depois de alterar `models.py`, sempre gere e aplique a migração antes de considerar a tarefa
concluída — models sem migração correspondente quebram `manage.py check` e `migrate`.

## Stack de produção (`docker-compose.prod.yml`)

Existe uma segunda stack, completa e separada da de dev: `docker-compose.prod.yml` +
`backend/Dockerfile.prod` + `backend/entrypoint.prod.sh` + `frontend/Dockerfile.prod` +
`frontend/nginx.conf`. Ela serve o backend via `gunicorn` (com `collectstatic`/`whitenoise` pros
estáticos do Django Admin) e o frontend como build estático do Vite atrás de um `nginx` que faz
proxy de `/api/`, `/admin/` e `/static/` pro backend — pensada pro deploy real (hoje, servidor
interno atrás de túnel Cloudflare, sempre HTTPS).

```bash
docker compose -f docker-compose.prod.yml up -d --build
docker compose -f docker-compose.prod.yml exec backend python manage.py migrate   # manual, de propósito
```

Regra que vale pra qualquer mudança futura: **a stack de dev (`docker-compose.yml`,
`backend/Dockerfile`, `frontend/Dockerfile`) nunca deve ser alterada por causa de uma necessidade
da stack de produção** — são arquivos irmãos, não uma variação um do outro. Única exceção:
mudanças em `backend/config/settings.py` são compartilhadas (é um arquivo só pros dois ambientes),
mas sempre precisam continuar seguras em `DEBUG=True` sem efeito nenhum no `runserver`/dev.

`docker compose -f docker-compose.prod.yml exec backend pytest` não funciona de propósito (a
imagem de produção só instala `requirements.txt`, sem `pytest`/`black`/`ruff`) — testes continuam
rodando só na stack de dev.

## Antes de considerar uma mudança de backend pronta

1. `docker compose exec backend python manage.py makemigrations` (se mexeu em models) e
   `migrate`.
2. `docker compose exec backend black . && docker compose exec backend ruff check --fix .`
3. `docker compose exec backend python manage.py check`
4. `docker compose exec backend pytest`

Todos os 4 devem passar limpos (sem erros, sem "would reformat") antes de reportar a tarefa como
concluída.

## Antes de considerar uma mudança de frontend pronta

1. `docker compose exec frontend npm run build` (`tsc -b && vite build`) — typecheck e build de
   produção devem passar sem erro. Ainda não há lint/formatter/testes configurados no `frontend/`;
   até existirem, esse é o único gate automatizado.
2. Testar o fluxo manualmente no navegador (`http://localhost:5173`, stack já no ar via
   `docker compose up -d`) — cobrindo o caminho feliz e casos de borda da tela alterada. Typecheck
   não substitui isso.

## Convenções

- Formatação: `black` (line-length 110). Lint: `ruff` (regras `E`, `F`, `I`; migrations excluídas
  do lint). Config em `backend/pyproject.toml`.
- Commits/branches: `tipo/descrição` (ex.: `feat/allocation-closure-validator`,
  `fix/hierarchy-scope-leak`), seguindo o padrão já usado na branch atual.
- Comentários no código: só quando explicam um porquê não-óbvio (ex.: uma decisão pendente como
  O5, uma invariante escondida). Não comente o óbvio.
- Regras de negócio (fechamento exato, isolamento de escopo) pertencem a **services de domínio**,
  não a views/serializers — mantém o monólito modular descrito em
  [docs/architecture.md](docs/architecture.md).
- Título de página vive só no cabeçalho (`Topbar`, `frontend/src/components/ui/Topbar.tsx`), nunca
  duplicado dentro do corpo da página. Ao criar uma tela nova em `frontend/src/pages/`, **não**
  renderize `<h1>`/subtítulo próprio (nada de um bloco tipo `dp-header`/`dp-title`/`dp-subtitle`
  dentro do JSX da página) — em vez disso, adicione uma entrada no mapa de rótulos certo dentro de
  `Topbar.tsx` (`ADMIN_LABELS`, `NIVEL_LABELS`, `ACOMPANHAMENTO_LABELS` ou `FINALIZACAO_LABELS`,
  conforme o grupo de rotas) com o texto do título; se o rótulo variar por nível de quem está
  logado (ex.: `distribuirMetasLabel` em `frontend/src/components/distribuicaoLabels.ts`), resolva
  isso dentro do próprio `Topbar`/helper compartilhado, não copiando a lógica pra dentro da página.
  A página em si começa direto pelo conteúdo (filtros, cards, tabela). Exceção: rotas públicas sem
  `Topbar` (login, esqueci senha, redefinir senha) — lá o título é a única fonte, então fica na
  própria página.
- Todo usuário criado (tela Gestão → Usuários / `UserAccountSerializer`) já nasce vinculado à sua
  posição real na hierarquia no mesmo ato de criação — nunca deixe um usuário "solto" pra vincular
  depois (exceto Administrador puro, sem posição na cascata). Não existe mais seletor manual de
  nó: o formulário só pede Nome completo + Cargo + Superior imediato, e
  `UserAccountSerializer._sync_position` resolve o `HierarchyNode` sozinho (reaproveita um nó
  livre com nome/cargo/superior batendo, ou cria um novo) — ver Decisão 11 (revisão 2026-07-21)
  em [docs/decisions.md](docs/decisions.md).

## Nunca

- Nunca commite `.env` (só `.env.example`) nem qualquer segredo real (senha de banco, chave do
  Django, credenciais do Postgres externo do histórico de vendas).
- Nunca escreva no Postgres externo do histórico de vendas — acesso é **somente leitura**, via
  `SalesHistoryProvider` (ver anticorruption layer em
  [docs/architecture.md](docs/architecture.md)). A fonte real já está conectada e sincronizada
  (Decisão 9); não existe mais Fake Provider no código.
- Nunca permita que uma alocação (`GoalAllocation`) seja persistida sem fechar 100% com a
  alocação-pai — é a invariante central do produto.
- Nunca ajuste layout/estilo da tela de login (`frontend/src/pages/LoginPage.tsx` e as classes
  `.login-*` em `frontend/src/index.css`) por conta própria — o visual (paleta, alinhamento do
  bloco de marca com o card, etc.) já foi validado e fechado com o usuário. Só mexa nela se o
  pedido mencionar explicitamente a tela de login.
- Nunca ajuste layout/estilo da tela Meta Gerencial (`frontend/src/pages/MetaGerencialPage.tsx` e
  as classes `.mg-*` em `frontend/src/index.css`) por conta própria — cards de sugestão (métricas
  vs. ano passado / vs. últimos 3 meses, painel expansível com histórico de 12 meses, KPIs do
  topo, total no rodapé) já foram iterados e fechados com o usuário. Só mexa nela se o pedido
  mencionar explicitamente a tela Meta Gerencial.
- Por padrão, não altere dados de hierarquia (nós, nomes, papéis, vínculo pessoa↔posição) nem de
  catálogo (grupos/subgrupos de produtos) por conta própria — nem via `seed_demo`, Django shell,
  fixture, migração de dados ou update direto no banco. Essas entidades devem mudar pela mão do
  próprio usuário, interagindo com a ferramenta (tela Gestão → Hierarquia/Usuários/Catálogo). Se o
  usuário pedir uma correção (nome errado, pessoa errada num nó, grupo/subgrupo incorreto),
  **oriente como fazer pela UI** (qual tela, qual botão) em vez de executar a mudança você mesmo.
  **Exceção**: se o usuário exigir explicitamente que você mesmo execute a mudança (não só
  descrever o problema, mas instruir a fazer), pode executar — mas confirme antes exatamente o que
  vai mudar. Atenção pro caso de exclusão: a API não expõe exclusão de nó/usuário (só
  `ativo=False`/`is_active=False`, de propósito — `parent` é `on_delete=PROTECT`), então uma
  exclusão de verdade exige ir direto no banco/shell e pode falhar ou quebrar histórico se o
  registro ainda for referenciado por outro nó-filho ou por alocações já feitas — avise o usuário
  desse risco antes de agir, e prefira desativar em vez de apagar sempre que o resultado prático
  for o mesmo (some da árvore/lista ativa, sem perder o histórico).
