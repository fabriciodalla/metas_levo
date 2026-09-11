# Metas Levo

App web interno da Levo Alimentos para distribuir metas comerciais de vendas (em KG inteiro),
em cascata, do Gerente até cada Vendedor — com fechamento exato e auditável em cada repasse.

> Visão completa do produto e da arquitetura: [docs/PROJECT.md](docs/PROJECT.md).
> Uso interno e proprietário da Levo Alimentos — sem licença pública.

## Stack

- **Backend:** Python 3.12, Django 5.2 LTS + Django REST Framework, testes com `pytest-django`.
- **Banco da aplicação:** PostgreSQL 16.
- **Frontend:** React SPA (Vite + TypeScript), sessão autenticada via cookie — ver `frontend/`.
- **Ambiente de desenvolvimento:** Docker Compose (obrigatório — todo o projeto roda em containers).

## Estrutura do repositório

```
.
├── backend/                 # Projeto Django (monólito modular)
│   ├── config/               # settings, urls, wsgi/asgi
│   └── apps/
│       ├── accounts/          # User customizado (login próprio, is_admin, vínculo à hierarquia)
│       ├── hierarchy/         # HierarchyNode + HierarchyClosure (árvore de 4 níveis) + seed_demo
│       ├── catalog/           # ProductGroup / ProductSubgroup / Product / ExternalProductMapping
│       ├── cycles/            # Cycle (ciclo mensal, aberto/fechado)
│       ├── allocations/       # GoalAllocation (repasse de meta encadeado ao pai)
│       ├── audit/             # AuditLogEntry (histórico de mudanças em hierarquia e catálogo)
│       └── sales_history/     # Espelho local do acumulado/carteira do Postgres externo
├── frontend/                # SPA React (Vite + TypeScript) — grade de distribuição + tela do Admin
├── docker-compose.yml
├── .env.example
└── docs/                     # Documentação do projeto (entry point: docs/PROJECT.md)
```

## Como rodar (desenvolvimento)

Pré-requisitos: Docker Desktop instalado e rodando. Não é necessário instalar Python ou Node
localmente — tudo roda em containers.

```bash
cp .env.example .env
docker compose up -d
```

A API sobe em `http://localhost:8001/`. O Django Admin fica em `http://localhost:8001/admin/`. O
frontend (SPA React) sobe em `http://localhost:8085/`.

### Cenário de demonstração

Popula um cenário mínimo (hierarquia de 4 níveis, catálogo, ciclo aberto, usuários e uma alocação
pendente) para explorar a tela de distribuição sem montar tudo manualmente:

```bash
docker compose exec backend python manage.py seed_demo
```

Cria os usuários `admin`/`admin12345` (Administrador), `gerente`, `local`, `supervisor` (senha
`senha12345` para os três).

### Usuário Administrador

O Administrador é quem gerencia hierarquia, catálogo e consulta o histórico de mudanças
(`AuditLogEntry`) — via Django Admin, até existir uma tela dedicada (ver
[docs/roadmap.md](docs/roadmap.md)). Criar um requer dois passos, porque são dois flags
independentes: o acesso ao painel do Django (`is_staff`/`is_superuser`) e o escopo
administrativo da própria aplicação (`is_admin`, ver
[docs/architecture.md](docs/architecture.md#isolamento-de-escopo-por-ramo)).

```bash
docker compose exec backend python manage.py createsuperuser

docker compose exec backend python manage.py shell -c "
from django.contrib.auth import get_user_model
user = get_user_model().objects.get(username='SEU_USUARIO')
user.is_admin = True
user.save()
"
```

### Histórico de vendas (Postgres externo)

Requer `SALES_HISTORY_DATABASE_URL` preenchido no `.env` (formato
`postgres://USUARIO:SENHA@HOST:PORTA/BANCO`, credenciais read-only). Sincroniza acumulado de
vendas e carteira de clientes para tabelas locais (`AccumulatedSale`, `ClientPortfolioSnapshot`) e
recalcula a base de distribuição (`DistributionBaseline`) — ver
[docs/architecture.md](docs/architecture.md#integração-com-o-postgres-externo-anticorruption-layer).

```bash
docker compose exec backend python manage.py sync_sales_history            # default: mês atual + anterior
docker compose exec backend python manage.py sync_sales_history --months=12

# só recalcula DistributionBaseline a partir do que já está sincronizado (sem tocar o externo)
docker compose exec backend python manage.py rebuild_distribution_baseline
```

### Comandos úteis

```bash
# aplicar migrações
docker compose exec backend python manage.py migrate

# criar migrações após alterar models
docker compose exec backend python manage.py makemigrations

# rodar os testes
docker compose exec backend pytest

# checar o projeto (erros de configuração/models)
docker compose exec backend python manage.py check

# formatar e lintar
docker compose exec backend black .
docker compose exec backend ruff check --fix .

# derrubar o ambiente
docker compose down
```

Portas expostas configuráveis via `.env` (`WEB_HOST_PORT`, `POSTGRES_HOST_PORT`,
`FRONTEND_HOST_PORT`), para evitar conflito com outros projetos locais.

## Documentação

| Arquivo | Conteúdo |
|---|---|
| [docs/PROJECT.md](docs/PROJECT.md) | Visão do produto, problema, fluxo, stack — comece por aqui |
| [docs/architecture.md](docs/architecture.md) | Componentes, invariantes de fechamento/completude, isolamento de escopo |
| [docs/data-model.md](docs/data-model.md) | Entidades e relações |
| [docs/decisions.md](docs/decisions.md) | Decisões de arquitetura e alternativas descartadas |
| [docs/open-questions.md](docs/open-questions.md) | Pendências de cálculo e perguntas em aberto — **não bloqueiam o desenvolvimento** |
| [docs/roadmap.md](docs/roadmap.md) | Fora de escopo nesta versão e próximos passos |
| [CLAUDE.md](CLAUDE.md) | Guia para agentes de IA trabalhando neste repositório |
