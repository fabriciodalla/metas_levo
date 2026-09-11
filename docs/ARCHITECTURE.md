eu 

# Arquitetura — Metas Levo

> Entry point: [PROJECT.md](./PROJECT.md). Fonte de verdade: `docs/kickoff/01-problem-brief.md` e
> `docs/kickoff/02-solution-design.md`. Projeto construído do zero — sem herança de versões anteriores.

## Estilo: monólito modular

Não microserviços. Justificativa ligada ao brief:

- Base de usuários pequena e conhecida (1 Gerente, 9 Locais, supervisores/vendedores —
  dezenas a poucas centenas).
- O fechamento exato é uma invariante **ACID por natureza**: exige consistência transacional forte.
- Time de manutenção enxuto.

Microserviços introduziriam consistência distribuída sem nenhum benefício exigido pelo brief.

## Diagrama de alto nível

```
┌──────────────────────────────────────────────────────────────────┐
│                 Navegador (usuário interno)                        │
│  UI de distribuição (grade com validação de soma em tempo real)    │
└───────────────┬────────────────────────────────────────────────────┘
                │ HTTPS / API JSON (sessão autenticada)
┌───────────────▼────────────────────────────────────────────────────┐
│                 APLICAÇÃO (monólito modular)                        │
│  [Auth/Sessão]  [Autorização de escopo por ramo]                   │
│                                                                    │
│  Módulo Hierarquia    Módulo Catálogo     Módulo Ciclos            │
│  (nós/pessoas)        (grupo/subgrupo)    (mês aberto/fechado)      │
│                                                                    │
│  Módulo Distribuição de Metas                                      │
│   ├─ DistributeGoalService (transação + invariante de fechamento)  │
│   ├─ ClosureValidator (soma == recebido, KG inteiro, >= 0)         │
│   ├─ CycleCompletenessChecker (100% chega ao Vendedor)             │
│   ├─ DistributionStrategy (PLUGÁVEL) ── RoundingPolicy (PLUGÁVEL)   │
│   └─ SuggestionService (usa histórico via porta abaixo)            │
│                                                                    │
│  Porta: SalesHistoryProvider (interface)                           │
│   └─ Adaptador Postgres externo (Anticorruption Layer)             │
└──────┬──────────────────────────────────────┬──────────────────────┘
       │ read/write                            │ SOMENTE LEITURA
┌──────▼───────────────┐            ┌──────────▼──────────────────────┐
│ Postgres da APLICAÇÃO │            │ Postgres EXTERNO (histórico)    │
│ (hierarquia, ciclos,  │            │ schema DESCONHECIDO — ver O3;   │
│  metas, usuários)     │            │ acesso via adaptador read-only  │
└───────────────────────┘            └─────────────────────────────────┘
```

## Componentes

| Componente                              | Responsabilidade                                                                                                                                           | Tecnologia                                             |
| --------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------ |
| Frontend de distribuição              | Grade de distribuição com feedback de soma em tempo real; telas por nível                                                                               | React SPA (Vite + TypeScript)                          |
| Painel do Administrador                 | CRUD de hierarquia, usuários, catálogo; visão geral, metas por nível, pendências, pré-processamento                                                  | React SPA (`/admin`), API DRF `IsAppAdmin`         |
| Backend / API                           | Regras de negócio, invariantes, autorização                                                                                                             | Django 5.2 LTS + DRF (Python)                          |
| Módulo Hierarquia                      | Árvore de nós/posições + closure table para consultas de subárvore                                                                                    | ORM + tabela de fechamento                             |
| Módulo Catálogo                       | Grupos e subgrupos de produto + mapeamento p/ fonte externa                                                                                                | ORM                                                    |
| Módulo Ciclos                          | Ciclo mensal e estado aberto/fechado                                                                                                                       | ORM                                                    |
| Módulo Distribuição                  | Serviço transacional, validador de fechamento, checador de completude, estratégias                                                                       | Serviços de domínio Python                           |
| Módulo Auditoria                       | Histórico de criação/inativação/mudança de vínculo em hierarquia e catálogo (`AuditLogEntry`)                                                    | ORM (GenericForeignKey)                                |
| Sincronização de histórico de vendas | Espelha acumulado + carteira do Postgres externo (`AccumulatedSale`, `ClientPortfolioSnapshot`) e deriva a base de cálculo (`DistributionBaseline`) | Conexão read-only isolada + management command mensal |
| Banco da aplicação                    | Dados transacionais da aplicação                                                                                                                         | PostgreSQL                                             |

Modelo de dados detalhado em [data-model.md](./data-model.md). Racional das escolhas em
[decisions.md](./decisions.md).

## API (DRF, sessão autenticada)

Endpoints de distribuição para a SPA (`frontend/`). CRUD de hierarquia/catálogo/usuários mora na
própria API desde a revisão da Decisão 4 — só os mapeamentos texto→entidade da Decisão 9
(`ExternalProductMapping`/`ExternalSalespersonMapping`) e `Product` (fora do MVP, O1) continuam
só no Django Admin.

| Endpoint                                    | Método            | O que faz                                                                                                                                                                               |
| ------------------------------------------- | ------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `/api/auth/csrf/`                         | GET                | Garante o cookie`csrftoken` antes do login (público)                                                                                                                                 |
| `/api/auth/login/`                        | POST               | Autentica por sessão (usuário/senha, sem SSO)                                                                                                                                         |
| `/api/auth/logout/`                       | POST               | Encerra a sessão                                                                                                                                                                       |
| `/api/auth/me/`                           | GET                | Usuário autenticado +`hierarchy_nodes` (lista — O5, 1:N)                                                                                                                            |
| `/api/hierarchy/nodes/`                   | GET/POST/PUT/PATCH | Nós visíveis (`HierarchyNode.objects.visible_to`); escrita só `IsAppAdmin`, sem `destroy` (inativa via `ativo=False`, nunca apaga); valida nível/pai e dispara O4 no update |
| `/api/accounts/users/`                    | GET/POST/PUT/PATCH | CRUD de usuário (`IsAppAdmin`): username, senha (opcional na edição), `is_admin`, `is_active`, `hierarchy_node_ids`; sem `destroy`                                         |
| `/api/cycles/`                            | GET                | Lista/detalhe de ciclos                                                                                                                                                                 |
| `/api/cycles/{id}/completeness/`          | GET                | `CycleCompletenessChecker` — alocações presas                                                                                                                                      |
| `/api/cycles/{id}/close/`                 | POST               | `CloseCycleService.close` (400 se incompleto)                                                                                                                                         |
| `/api/cycles/{id}/distribution-overview/` | GET                | `IsAppAdmin` — todas as alocações do ciclo, qualquer nível, para as telas de Metas/Pendências                                                                                    |
| `/api/cycles/{id}/export/`                | GET                | `IsAppAdmin` — CSV com a árvore inteira de alocações do ciclo                                                                                                                     |
| `/api/allocations/`                       | GET                | Alocações visíveis (`GoalAllocation.objects.visible_to`), filtro `?cycle=`                                                                                                       |
| `/api/allocations/{id}/distribute/`       | POST               | `DistributeGoalService.distribute` (400 em erro de fechamento/escopo)                                                                                                                 |
| `/api/allocations/{id}/reopen/`           | POST               | `ReopenAllocationService.reopen` (H4 — 400 se não distribuída, se o ciclo não está aberto, ou em erro de escopo)                                                                 |
| `/api/catalog/groups/`                    | GET/POST/PUT/PATCH | Grupos de produto; não-admin só vê ativos, admin vê todos; escrita`IsAppAdmin`, sem `destroy`                                                                                   |
| `/api/catalog/subgroups/`                 | GET/POST/PUT/PATCH | Subgrupos, filtro`?group=`; mesmas regras de visibilidade/escrita dos grupos                                                                                                          |
| `/api/catalog/products/`                  | GET                | Produtos ativos, filtro`?subgroup=` — só leitura (fora do MVP, O1)                                                                                                                  |
| `/api/sales-history/sync/`                | POST               | `IsAppAdmin` — dispara `SalesHistorySyncService` + rebuild do `DistributionBaseline`                                                                                             |

Toda escrita de `GoalAllocation`/`Cycle` passa pelos serviços de domínio já existentes — a view
nunca persiste diretamente (mesma regra do CLAUDE.md: regra de negócio fica no serviço). O CRUD de
hierarquia/catálogo/usuário segue o mesmo princípio onde há regra de negócio: a validação de
nível/pai fica no serializer (é forma de dado, não invariante de domínio) e o gatilho de
reatribuição (O4) fica em `HierarchyChangeReassignmentService.detect_and_reassign_if_needed`,
compartilhado entre o Django Admin e a API — ver Decisão 4 (revisão) em
[decisions.md](./decisions.md).

## Frontend (React SPA)

`frontend/` — Vite + TypeScript, sessão autenticada via cookie (sem SSO, ver Decisão 4).

- **Proxy em vez de CORS:** o dev server do Vite faz proxy de `/api/*` para `backend:8000`
  (`vite.config.ts`), então o navegador só fala com a origem do Vite — sem CORS, sem cookie
  cross-origin. Isso exige `backend` em `DJANGO_ALLOWED_HOSTS` (o proxy reescreve o `Host`) e a
  origem do Vite em `CSRF_TRUSTED_ORIGINS` (o `Origin` enviado pelo navegador não é reescrito).
- **`AuthContext`** — login/logout/me via `/api/auth/`; busca o cookie `csrftoken` antes do login.
- **Tela de distribuição** — lista as alocações do usuário (`owner_node` entre os `hierarchy_nodes`
  do usuário — O5, 1:N) pendentes vs. já distribuídas; formulário multi-linha (destino + quantidade, e
  subgrupo quando a granularidade quebra) com soma calculada no cliente antes de enviar —
  o `ClosureValidator` no backend continua sendo a fonte de verdade, o cliente só dá feedback.
- **Área do Administrador (`/admin/*`)** — layout com sub-rotas (Decisão 4, revisão), só para
  `is_admin`:
  - **Visão Geral** — progresso do ciclo aberto (completude, KG parado por nível).
  - **Gestão** — CRUD de hierarquia (criar/inativar nó, reparentar, trocar nível), usuários
    (criar/inativar, senha, papel admin, vínculo com nós) e catálogo (grupos/subgrupos). Sem botão
    de excluir em lugar nenhum — só inativar (`ativo`/`is_active`), para não perder vínculo
    histórico com alocações/auditoria.
  - **Metas** — meta distribuída agregada por nível hierárquico, por ciclo.
  - **Pendências** — quem ainda não distribuiu (alocações intermediárias com `distributed=False`).
  - **Pré-processamento** — dispara a sincronização do histórico de vendas (`/sales-history/sync/`)
    e reserva espaço para configurações futuras.
  - **Dashboard** — placeholder reservado para uso futuro.
  - Os mapeamentos da Decisão 9 (`ExternalProductMapping`/`ExternalSalespersonMapping`) continuam
    só no Django Admin — curadoria pontual, fora do escopo pedido para a área do Administrador.

## Invariante 1 — Fechamento exato (local, por repasse)

Regra rígida e não-negociável do brief: em cada nível, a soma distribuída para baixo fecha
**exatamente** com o recebido de cima, em KG inteiro, sem sobra nem falta. Garantida pela
arquitetura, **independente da fórmula**:

1. **`ClosureValidator`** (independente da estratégia): dada uma alocação-pai e as filhas propostas,
   rejeita se `soma(filhas.quantity_kg) != pai.quantity_kg`, se algum valor não for inteiro, ou se
   algum for `< 0`.
2. **`DistributeGoalService`** executa em **uma transação ACID**: computa os inteiros (via
   estratégia) → passa pelo `ClosureValidator` → só então persiste as filhas atomicamente. Falha na
   invariante = rollback; nunca existe estado parcial.
3. **Garantias de banco:** CHECK `quantity_kg >= 0` e tipo inteiro no schema; a soma é garantida na
   aplicação dentro da transação (reforçável por constraint deferida como defesa em profundidade).
4. Como qualquer estratégia (auto ou manual) desemboca no mesmo validador, **trocar a fórmula não
   pode quebrar o fechamento** — o pior caso de uma fórmula ruim é ser rejeitada, nunca persistir
   sobra/falta.

## Invariante 2 — Completude de ciclo (end-to-end, "100% chega ao Vendedor")

A Invariante 1 é **local**: garante que nada se perde num repasse, mas não garante que a meta
percorreu a árvore inteira até a ponta. Um ciclo poderia fechar com parcelas **presas** em níveis
intermediários. O critério "100% chega aos vendedores" é uma propriedade **end-to-end distinta**:

1. **Definição:** um ramo está completo quando toda folha da subárvore ativa de `GoalAllocation`
   pertence a um nó de `level == VENDEDOR`. Equivalente: nenhuma alocação de nível intermediário
   (GERENTE..SUPERVISOR) com `distributed == false`. Alocações no nível VENDEDOR são folhas legítimas.
2. **`CycleCompletenessChecker`** (serviço de domínio): percorre a árvore de alocações do ciclo
   (via `parent_allocation_id` + `HierarchyClosure`) e retorna os "pontos presos" (alocações
   intermediárias não distribuídas) e o total de KG parado por ramo.
3. **Onde roda:** (a) **assistivo** durante a distribuição — alimenta a UI com "faltam N KG para
   chegar à ponta"; (b) **gate de fechamento** — `CloseCycleService` invoca o checker dentro de uma
   transação e **recusa marcar o ciclo como FECHADO** enquanto houver alocação intermediária pendente.
4. Um ciclo FECHADO implica, por construção, soma exata em cada nível **e** 100% da meta no nível
   VENDEDOR. Um nó desativado com meta em ciclo aberto é coberto por O4/Decisão 10
   (`HierarchyChangeReassignmentService` reabre a alocação-pai automaticamente); "ramo sem vendedor
   ativo" continua sem tratamento dedicado — se todos os vendedores de um ramo forem inativados sem
   que o nó deles seja desativado/movido, nada dispara reatribuição automaticamente.

## Isolamento de escopo por ramo

Princípio: **imposto na camada de dados, nunca só na UI.**

1. **Leitura:** todo acesso a `HierarchyNode`/`GoalAllocation` passa por um manager/repositório base
   que filtra por `owner_node_id ∈ descendentes(nós_do_user)` (inclusive os próprios nós), resolvido
   via `HierarchyClosure`. Ramos irmãos ficam invisíveis por construção da query.
2. **Escrita (object-level):** distribuir só é permitido quando `allocation.owner_node` está entre os
   nós do usuário (só distribuo o que recebi) e os destinos são **filhos diretos** do meu nó.
   Checagem no serviço de domínio, não na view.
3. **Admin:** `is_admin` tem escopo próprio (gestão de estrutura), separado da cadeia de distribuição.
4. **Defesa em profundidade:** testes automatizados de isolamento (um usuário de um ramo nunca resolve
   nós/metas de outro ramo) como critério de aceite verificável.
5. **User↔Node é 1:N (O5, Decisão 10):** `User.hierarchy_nodes` (`ManyToManyField`) — uma pessoa
   pode ocupar mais de uma posição/ramo ao mesmo tempo. Leitura e escrita (itens 1 e 2 acima) unem
   **todos** os nós do usuário, não comparam contra um único nó. `ScopeResolver.descendant_ids()`
   aceita um id ou uma lista de ids. Testado em `MultiNodeUserScopeTests`
   (`apps/allocations/test_isolation.py`) — um usuário com 2 nós em ramos diferentes vê a união dos
   dois, e pode distribuir/reabrir alocações de qualquer um deles.

## Integração com o Postgres externo (Anticorruption Layer)

O3 foi respondida pelo usuário: credenciais read-only e as duas queries reais (acumulado e
carteira) já foram fornecidas e validadas contra o schema (`stage`/`stage_comercial`). Decisão
tomada: **sem Fake Provider** — construído direto contra o schema real, com sincronização
periódica em vez de leitura ao vivo:

- **Conexão dedicada, somente leitura:** alias `sales_history` em `DATABASES`
  (`SALES_HISTORY_DATABASE_URL` no `.env`, nunca commitado), isolada do banco da aplicação. Nenhum
  model/migração é atribuído a esse alias — só cursor bruto.
- **`SalesHistorySyncService`** (`apps/sales_history/services.py`): roda as duas queries
  (verbatim, preservadas em `queries.py`) contra o alias externo e grava o resultado em duas
  tabelas locais no banco da aplicação — `AccumulatedSale` e `ClientPortfolioSnapshot`.
- **Sem mapear as tabelas externas como models do domínio** — a aplicação nunca faz join direto
  no schema externo; só lê as duas tabelas locais depois de sincronizadas.
- **`sync_sales_history`** (management command): roda a sincronização. Janela do acumulado é
  parâmetro (`--months`, default **12** — janela confirmada com o usuário, H2 resolvida, ver
  [decisions.md](./decisions.md#decisão-6--fórmula-de-cálculo-para-p1-p4-tendência--sazonalidade-sobre-12-meses));
  carteira é sempre substituída por inteiro (foto do momento, sem histórico).
- **Cadência:** mensal, rodado manualmente ou por cron simples — não precisa de fila de tarefas.
- **`DistributionBaseline`** (`DistributionBaselineService.rebuild()`): terceira tabela local,
  não espelhada do externo — derivada de `AccumulatedSale` + `ClientPortfolioSnapshot` via join
  por `client_code` (clifor). Reatribui cada venda ao vendedor **atual** da carteira do cliente,
  não a quem historicamente vendeu, e agrupa por (ano, mês, vendedor, subgrupo). Cliente sem
  vendedor vigente na carteira gera linha com `salesperson_name=NULL` em vez de ser descartado —
  ver Decisão 12. `total_quantity` do agrupamento é sempre KG inteiro (`ROUND_HALF_UP`). É a base
  que as fórmulas de distribuição (P1–P4) consomem. Rodada automaticamente ao final de
  `sync_sales_history`, ou isolada via `rebuild_distribution_baseline`.
- **Mapeamentos texto→entidade (O3, resolvido — ver Decisão 9):** `DistributionBaseline` só
  tem texto do ERP (`subgroup_name`, `salesperson_name`), sem código estável. Dois mapeamentos
  explícitos (curados manualmente, sem casamento automático por nome) ligam esse texto às
  entidades internas:
  - **`ExternalProductMapping`** (`apps/catalog/models.py`) — `subgroup_name` → `ProductSubgroup`
    (grupo vem de graça via `ProductSubgroup.group`).
  - **`ExternalSalespersonMapping`** (`apps/hierarchy/models.py`, novo) — `salesperson_name` →
    `HierarchyNode` (o vendedor).
- **`SalesHistoryProvider`** (`apps/sales_history/provider.py`) — a porta formal que faltava:
  resolve `DistributionBaseline` para séries `MonthlyQuantity` (ano, mês, quantidade) usando os
  dois mapeamentos acima. `group_history(group_id, ...)` soma todos os subgrupos mapeados de um
  grupo (P1) **sem filtrar por vendedor** — por isso inclui as linhas `salesperson_name=NULL`
  (Decisão 12): o Gerente vê o volume real do grupo, mesmo a fração sem vendedor titular na
  carteira no momento. `target_history(hierarchy_node_id, ...)` soma o histórico de todo Vendedor
  descendente de um nó (via `ScopeResolver`/`HierarchyClosure`), filtrado por
  `salesperson_name__in=[nomes mapeados]` — linhas `NULL` nunca casam com um nome específico, então
  ficam de fora de P2-P4 (correto: sem vendedor vigente não dá pra atribuir a um Vendedor/Supervisor
  específico). Meses sem dado entram com `quantity_kg=0` (série sem buracos, como as estratégias
  exigem). Consumido diretamente por `SeasonalTrendSuggestionStrategy`/
  `SeasonalTrendDistributionStrategy` — provado por teste de ponta a ponta em
  `apps/sales_history/test_provider.py`.

## Ponto de extensão para as fórmulas (plugável)

Das 5 pendências de cálculo (ver [open-questions.md](./open-questions.md)), **todas têm fórmula
aprovada** — P1-P4 (Decisão 6) e P5 (Decisão 7). Fórmula aprovada não significa fórmula travada:
continuam plugáveis por design, caso alguma precise ser revista depois.

- **`DistributionStrategy`** — dado `total_kg` (inteiro recebido) e a lista de alvos (filhos diretos +
  contexto, ex.: histórico via `SalesHistoryProvider`), retorna `{alvo: quantidade_kg}`. Cobre
  distribuição Gerente→Local (P2), quebra Grupo→Subgrupo (P3), distribuição Supervisor→Vendedor (P4).
  Implementação aprovada: `SeasonalTrendDistributionStrategy` — decomposição clássica (tendência
  linear × índice sazonal por mês, 12 meses de histórico) vira peso relativo por alvo, fechado em
  KG inteiro via `RoundingPolicy`. Registrada como modo `AUTO` para GERENTE/LOCAL/SUPERVISOR.
- **`SuggestionStrategy`** — sugestão automática de metas por grupo para o Gerente (P1), a partir do
  histórico de **12 meses** (H2 resolvida — ver Decisão 6). Implementação aprovada:
  `SeasonalTrendSuggestionStrategy` (mesma decomposição tendência+sazonalidade, valor absoluto).
- **`RoundingPolicy` / `RemainderAllocation`** — transforma proporções fracionárias em KG inteiro **e
  aloca o resto** para fechar exatamente. É o núcleo do conflito "fração natural vs. inteiro exato".
  Implementação aprovada (P5, Decisão 7): `LargestRemainderRoundingPolicy` — método do maior resto /
  Hamilton, arredonda toda proporção para baixo e distribui o KG restante, um de cada vez, para
  quem tem a maior fração perdida.
- **Modo manual** é apenas uma estratégia onde o usuário fornece os valores; passa pelo mesmo validador.
- **Registry** seleciona a estratégia por nível e por modo (auto/manual).
- **Default reaproveitado:** `LargestRemainderRoundingPolicy` é usada como `RoundingPolicy` default
  pelas estratégias `AUTO` de P1-P4, mas continua injetável — trocar por outra política não exige
  mudar `SeasonalTrendDistributionStrategy`.
- **Ligação ao dado real (O3, resolvida):** `SalesHistoryProvider` (ver seção "Integração com o
  Postgres externo" acima) resolve `DistributionBaseline` para as séries `MonthlyQuantity` que
  `SeasonalTrendSuggestionStrategy`/`SeasonalTrendDistributionStrategy` esperam — via
  `ExternalProductMapping`/`ExternalSalespersonMapping`. Falta só popular esses mapeamentos com os
  dados reais (curadoria manual, Django Admin) e ligar um endpoint/UI que chame o registry em modo
  `AUTO` — nenhuma peça técnica de cálculo falta.
- **Sugestão sempre revisável:** o resultado de qualquer `AUTO` (P1-P4) é usado para pré-preencher a UI
  de distribuição — o usuário do nível aprova como está ou corrige valores específicos antes de
  confirmar. A persistência sempre passa pelo mesmo `DistributeGoalService`/`ClosureValidator`
  independente da origem (manual ou auto).

## Reabertura em cascata (H4) — IMPLEMENTADO

Enquanto o ciclo está ABERTO, o nível que distribuiu uma alocação pode reabri-la e refazer o
repasse, sem aprovação formal (hipótese H4). Implementado em `ReopenAllocationService`
(`backend/apps/allocations/services.py`) e exposto via `POST /api/allocations/{id}/reopen/`.

1. **Invalidação do ramo afetado:** `ReopenAllocationService.reopen()` percorre toda a sub-árvore
   de filhas abaixo da alocação (via `parent_allocation`, nível por nível) e **apaga** essas linhas
   (da folha mais profunda para cima, respeitando `on_delete=PROTECT`), registrando o que foi
   invalidado num `AuditLogEntry` (ação `REABERTURA`) antes de apagar — ver Decisão 8 em
   [decisions.md](./decisions.md). A alocação volta a `distributed == false`. Como as filhas são
   removidas por completo (não ficam meio-criadas), nenhuma soma inconsistente sobrevive à
   transação — a Invariante 1 nunca fica violada no estado persistido.
2. **Volta a incompleto:** com `distributed == false` de novo, o `CycleCompletenessChecker` passa a
   reportar o ciclo como incompleto (se a alocação não for de nível VENDEDOR) e ele não pode fechar
   até uma nova chamada de `DistributeGoalService.distribute()` — **sem nenhuma mudança nesse
   serviço** — refazer o repasse até o Vendedor.
3. **Escopo mínimo:** só a sub-árvore da alocação reaberta é invalidada; a alocação-pai e irmãos não
   tocados permanecem válidos (testado em `ReopenAllocationServiceTests.test_reopen_is_scoped_to_the_branch_only`).
4. **Guarda de escopo e de ciclo:** só quem possui a alocação (`owner_node` entre os `hierarchy_nodes`
   do usuário — O5, 1:N) pode reabri-la, e só se o ciclo estiver `ABERTO` — caso contrário
   `AllocationScopeError`/`AllocationReopenError` (400 na API).
5. **Reatribuição automática por mudança de hierarquia (O4, Decisão 10) — IMPLEMENTADO:** quando um
   nó é desativado ou reparentado (`HierarchyNodeAdmin.save_model()` detecta a transição) enquanto
   tem meta em ciclo `ABERTO`, `HierarchyChangeReassignmentService.reassign_open_cycle_allocations()`
   reabre automaticamente a alocação-**pai** (via `ReopenAllocationService.reopen_for_hierarchy_change()`,
   que reaproveita a mesma cascata acima mas pula a checagem de posse — quem aciona é o
   Administrador mudando a hierarquia, não o dono da alocação). A meta volta pro nó pai, que precisa
   redistribuir considerando a mudança; quem redistribui decide para onde vai a parte do nó
   afetado.

## Rastreabilidade — como cada restrição do brief é atendida

| Restrição                                           | Como a solução atende                                                                               |
| ----------------------------------------------------- | ----------------------------------------------------------------------------------------------------- |
| Web interno                                           | Monólito modular servindo SPA + Django Admin                                                         |
| Login próprio usuário/senha, sem SSO                | Módulo Auth com credenciais próprias; sem IdP externo                                               |
| Ciclo mensal                                          | Entidade`Cycle` (ano, mês, status), unicidade por mês                                             |
| KG sempre inteiro                                     | `quantity_kg` inteiro + CHECK; validador rejeita não-inteiros                                      |
| Fechamento exato (rígido)                            | `ClosureValidator` + serviço transacional, independente da fórmula                                |
| 100% chega aos vendedores                             | `CycleCompletenessChecker` como gate de fechamento                                                  |
| Isolamento de escopo por ramo                         | Filtro por subárvore (closure table) na camada de dados + checagem object-level                      |
| Hierarquia fixa de 4 níveis, granularidade variável | `HierarchyNode.level` + `granularity` por alocação                                              |
| Fórmulas plugáveis sem retrabalho                   | Interfaces Strategy/RoundingPolicy + registry                                                         |
| Auditabilidade                                        | Encadeamento`parent_allocation` + metadados de auditoria                                            |
| Reabertura consistente (H4)                           | Invalidação em cascata do ramo + retorno a "incompleto" —`ReopenAllocationService`, implementado |
