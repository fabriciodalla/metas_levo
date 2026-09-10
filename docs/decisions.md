# Decisões-Chave — Metas Levo

> Entry point: [PROJECT.md](./PROJECT.md). Registro das decisões de arquitetura, alternativas
> descartadas e reversibilidade. Fonte: `docs/kickoff/02-solution-design.md`.

## Decisão 1 — Stack de backend / arquitetura (mais cara de reverter)
- **Escolha:** **Django 5.2 LTS + Django REST Framework**, como monólito modular, com camada de
  domínio explícita (services) para as regras de meta. **Linguagem Python**, alinhada à preferência
  declarada do usuário (backend + testes em Python).
- **Alternativas descartadas (todas em Python, respeitando a preferência):**
  - (B) FastAPI + SQLAlchemy + SPA React.
  - (C) Django "puro" (templates/HTMX) sem SPA.
  - *(Contraste, fora da preferência): stack TypeScript full-stack (Next.js), descartado por não
    atender à preferência de linguagem Python.)*
- **Trade-offs ligados ao brief:**
  - *Manutenção da hierarquia por Administrador (hipótese):* o **Django Admin** entrega o CRUD de
    hierarquia/pessoas/catálogo quase de graça; em (B) esse painel seria construído à mão.
  - *Fonte de histórico em Postgres externo separado:* Django tem suporte **nativo a múltiplos bancos**
    (database routers), encaixando "banco externo read-only separado" sem gambiarra; (B) exige montar
    isso na mão.
  - *5 fórmulas plugáveis:* Python é forte para lógica numérica/estratégias e é a linguagem preferida.
  - *Invariante transacional:* ambos suportam ACID — empate técnico.
  - *Contra Django:* mais opinado/pesado que FastAPI para API pura — mitigado pelo Django Admin, que
    paga esse custo devolvendo o CRUD do Administrador.
- **Reversibilidade:** **cara de reverter** (define framework, ORM, forma dos serviços e o painel
  admin). A linguagem Python em si está estabilizada pela preferência do usuário.
- **Ressalva (ver O2):** implementações anteriores (FastAPI/Next.js e Django) foram descartadas e o
  **motivo do descarte é desconhecido** por restrição da task. Isso **não contradiz** a preferência
  declarada por Python. Permanece só como transparência, não como veto pendente.

## Decisão 2 — Modelo da cascata com alocação encadeada (invariante)
- **Escolha:** cada repasse é uma `GoalAllocation` que aponta para a alocação-pai
  (`parent_allocation`). A invariante de fechamento é local a cada alocação:
  *soma das filhas diretas == quantidade do pai*.
- **Alternativas descartadas:** (B) armazenar só valores por nó sem vínculo pai-filho e recalcular
  somas por convenção; (C) event sourcing das distribuições.
- **Trade-offs:** o encadeamento pai-filho torna a invariante **verificável e auditável por linha**
  (atende "auditável") e simplifica reabertura. (B) perde rastreabilidade da origem de cada meta;
  (C) é poderoso para auditoria mas caro/complexo demais para o MVP.
- **Reversibilidade:** média — é o coração do modelo de dados; escolhido junto da Decisão 1 como parte
  do núcleo arriscado.

## Decisão 3 — Banco da aplicação: PostgreSQL
- **Escolha:** PostgreSQL para o banco da aplicação (separado do Postgres externo de histórico).
- **Alternativas descartadas:** MySQL/MariaDB; SQLite.
- **Trade-offs:** Postgres oferece CTEs recursivas (subárvore para isolamento), constraints ricas
  (CHECK para KG inteiro/`>= 0`), transações robustas e homogeneidade operacional com a fonte já
  existente. SQLite não serve para concorrência multiusuário de escrita.
- **Reversibilidade:** média (fácil se o acesso a dados ficar atrás de repositórios).

## Decisão 4 — Frontend: React SPA + Django Admin
- **Escolha:** SPA React (Vite + TypeScript) para as telas de distribuição; **Django Admin** para o
  CRUD do Administrador. Escolha do framework de frontend feita para atender às **duas prioridades
  declaradas pelo usuário**.
- **Alternativas descartadas:** Django templates + HTMX (server-rendered, mais enxuto); Next.js (SSR).
- **Trade-offs — amarrados às prioridades do usuário:**
  - *Prioridade 1 — fácil de configurar/rodar:* Vite oferece setup mínimo e rápido (dev server
    instantâneo, build simples, sem a complexidade de um framework SSR). Reaproveitar o Django Admin
    elimina construir a tela mais chata do sistema — zero setup adicional.
  - *Prioridade 2 — visualmente atraente:* a grade de distribuição precisa de feedback de soma em
    tempo real ("falta/sobra X KG"), estados interativos e edição fluida — em que uma SPA React entrega
    UI rica com naturalidade. HTMX seria mais enxuto porém com UX de edição em grade menos fluida.
- **Nota:** a **biblioteca/design system de UI ainda NÃO está decidida** e **não é registrada como
  aprovada** — será escolhida na implementação, otimizando setup fácil + aparência atraente. O que está
  fixado é o framework de renderização (React SPA via Vite/TS), não uma lib de componentes.
- **Reversibilidade:** fácil (contrato via API JSON isola o frontend do backend).
- **Revisão (o CRUD migrou do Django Admin pra SPA):** o usuário pediu explicitamente que o
  Administrador tenha uma tela própria, dentro da SPA, para gerir hierarquia (criar/inativar nós,
  reparentar, trocar de nível), usuários (criar/inativar, papel admin, senha, vínculo com nós) e
  catálogo (grupos/subgrupos). Isso reverte a metade "Django Admin" desta decisão — o framework de
  frontend (React SPA/Vite) continua o mesmo, só a **superfície de CRUD** mudou de dono.
  - **Motivo:** H3 ("Administrador cuida de toda a gestão dentro da ferramenta", ver
    [PROJECT.md](./PROJECT.md)) já apontava nessa direção; o usuário confirmou que quer isso
    consolidado numa tela só, sem alternar para o Django Admin no dia a dia.
  - **O que ficou só no Django Admin:** os mapeamentos texto→entidade da Decisão 9
    (`ExternalProductMapping`, `ExternalSalespersonMapping`) — curadoria pontual, não citada no
    pedido do usuário, sem tela dedicada por ora. `Product` (fora do MVP, O1) também não ganhou CRUD.
  - **Implementação:** `HierarchyNodeViewSet`/`ProductGroupViewSet`/`ProductSubgroupViewSet` viraram
    CRUD (sem `destroy` — só inativar via `ativo=False`, nunca apagar) com escrita restrita a
    `IsAppAdmin`; novo `UserAccountViewSet` (`apps/accounts`) cobre CRUD de usuário. O gatilho de O4
    (`HierarchyChangeReassignmentService`, Decisão 10) que antes só existia em
    `HierarchyNodeAdmin.save_model` foi extraído para
    `HierarchyChangeReassignmentService.detect_and_reassign_if_needed()`, chamado tanto pelo Django
    Admin quanto pelo `perform_update` do novo viewset — a checagem de transição
    (desativado/reparentado) fica centralizada em vez de duplicada.
  - **Reversibilidade:** fácil — a checagem de transição está isolada num único método; voltar a
    Django-Admin-only seria só parar de expor os novos endpoints de escrita.

## Decisão 5 — Ponto de extensão para as fórmulas (plugável)
- **Escolha:** interfaces `DistributionStrategy` + `RoundingPolicy`, selecionadas via registry por
  nível/modo (automático vs. manual).
- **Alternativas descartadas:** hardcode das fórmulas quando definidas; regras em banco/config.
- **Trade-offs:** a interface isola as 5 pendências de cálculo de todo o resto — definir cada fórmula
  depois vira implementar uma classe, **sem retrabalho estrutural**. Hardcode violaria a diretriz do
  brief.
- **Reversibilidade:** fácil, por design.

## Decisão 6 — Fórmula de cálculo para P1-P4: tendência + sazonalidade sobre 12 meses
> **Nota (2026-09-03):** esta é a fórmula original das 4 pendências, e continua sendo o que
> `SeasonalTrendSuggestionStrategy` usa para P1 (valor absoluto sugerido ao Gerente). Para P2-P4
> (proporção entre alvos), o default do modo `AUTO` passou a ser `RecentAverageDistributionStrategy`
> (média dos últimos 3 meses) — ver a revisão 2026-09-03 no fim desta seção antes de assumir que
> P2-P4 ainda usam tendência+sazonalidade por padrão.
- **Escolha atual:** para as 4 pendências de proporção (P1 sugestão ao Gerente, P2 Gerente→Local,
  P3 quebra Local→Supervisor, P4 Supervisor→Vendedor), a fórmula aprovada é uma **decomposição
  clássica multiplicativa (tendência linear × índice sazonal por mês do calendário)** sobre uma
  janela de **12 meses** de histórico, projetando o mês seguinte:
  1. **Tendência:** regressão linear simples (mínimos quadrados, método fechado — não é machine
     learning) sobre os 12 pontos, extrapolada um mês à frente.
  2. **Sazonalidade:** para cada mês do calendário presente no histórico, calcula-se a razão entre
     o valor real e o valor da reta de tendência naquele ponto; essas razões (normalizadas para
     média 1) formam o índice sazonal por mês.
  3. **Projeção:** tendência extrapolada × índice sazonal do mês seguinte.
  Em P1, o resultado é o valor absoluto sugerido por grupo. Em P2-P4, o resultado por alvo vira
  peso relativo, normalizado pelo `RoundingPolicy` (P5) para fechar exatamente com o total recebido.
  Implementado em `SeasonalTrendSuggestionStrategy` e `SeasonalTrendDistributionStrategy`
  (`backend/apps/allocations/strategies.py`), registradas como modo `AUTO` para os níveis
  GERENTE/LOCAL/SUPERVISOR (GERENTE incluído em 2026-07-22, ver refinamento abaixo; nível REGIONAL
  removido da hierarquia — ver Decisão 14).
- **Limitação conhecida e aceita pelo usuário:** com apenas 12 meses (1 ano) de histórico, cada
  índice sazonal por mês do calendário vem de **uma única observação** — não distingue padrão
  sazonal real de um evento pontual naquele mês específico (uma ruptura de estoque, uma promoção).
  Ficaria mais robusto com 24-36 meses (múltiplas observações por mês), mas o usuário confirmou
  operar com a janela de 12 meses disponível mesmo assim.
- **Histórico da decisão (revisões, mantidas para rastreabilidade):**
  - *Primeira versão descartada:* média móvel ponderada linear simples sobre 6 meses (sem
    sazonalidade) — descartada porque 6 meses nunca cobre os 12 meses do calendário, então
    nenhum índice sazonal seria calculável; a fórmula só refletia tendência recente.
  - *Revisão para 12 meses + fórmula mais aprimorada:* o usuário decidiu que a janela de 6 meses
    era só um parâmetro (não muda o algoritmo de média ponderada) e pediu explicitamente um
    cálculo mais aprimorado com 12 meses, escolhendo a combinação tendência + sazonalidade entre as
    opções apresentadas (as outras eram: só ajuste sazonal; só projeção de tendência).
  - *Alternativa descartada em ambas as rodadas:* Holt-Winters/suavização exponencial tripla —
    também não é ML, mas ajusta parâmetros (alpha/beta/gamma) por otimização, o que é menos
    transparente/auditável do que a decomposição clássica com componentes explícitos e inspecionáveis.
- **Trade-offs:** a decomposição clássica é auditável componente a componente (dá pra mostrar a um
  Gerente "a tendência projeta X, o índice sazonal de dezembro é Y% acima da média, então a
  sugestão é Z") e reage tanto a tendência recente quanto a padrão sazonal — mais completa que a
  versão anterior (só tendência). Em troca, tem mais camadas de cálculo para auditar e a
  sazonalidade é frágil com só 1 ano de dado (ver limitação acima).
- **Bloqueio conhecido:** as classes recebem a série histórica **já resolvida** por
  `ProductGroup.id`/`HierarchyNode.id` via parâmetro (`MonthlyQuantity` com `ano`/`mes` explícitos)
  — não fazem o casamento entre texto do ERP (`subgroup_name`, `salesperson_name`/`nk_vendedor`) e
  as entidades internas. Isso depende de O3/O5 (ver [open-questions.md](./open-questions.md)) e não
  foi inventado aqui.
- **Workflow associado (não uma decisão de arquitetura nova, só confirmação):** o resultado de
  P1-P4 é sempre uma **sugestão revisável** — o usuário do nível aprova como está ou corrige valores
  específicos antes de confirmar; nenhuma alocação é persistida sem esse toque humano, e o mesmo
  endpoint/serviço de `distribute` já existente cobre os dois casos (manual e auto).
- **Refinamento (2026-07-21) — peso de P2-P4 usa sempre o histórico do GRUPO inteiro, nunca de
  subgrupo:** para P2 (Gerente→Local), P3 (quebra Local→Supervisor) e P4 (Supervisor→Vendedor), o
  peso/proporção de cada alvo na fórmula de tendência+sazonalidade **é sempre calculado sobre o
  histórico agregado do grupo inteiro daquele alvo** — `SalesHistoryProvider.target_history(...,
  group_id=X, subgroup_id=None)` — **nunca** sobre o histórico de um subgrupo específico, mesmo nos
  níveis em que o repasse resultante é decomposto por subgrupo (P3) ou já nasce em granularidade
  SUBGROUP (P4, meta final do Vendedor). Quem for ligar o registry em modo `AUTO` a um endpoint (ver
  [open-questions.md](./open-questions.md), item "Frente ainda em aberto") não deve passar
  `subgroup_id` ao montar `history_by_target` para essas três pendências — só `group_id`.
  - **Motivo:** histórico por subgrupo (e mais ainda por combinação subgrupo × vendedor individual)
    é substancialmente mais esparso que o já limitado histórico de 12 meses por grupo — a mesma
    fragilidade do índice sazonal com uma única observação por mês, documentada acima, só que
    agravada por menos volume de dado por série. Pesar a distribuição por uma série tão rala
    arriscaria proporções mais ruidosas do que úteis.
  - **Fora de escopo aqui:** este refinamento resolve *qual histórico pesa a proporção entre
    alvos*, não *como a fatia de um alvo se decompõe em subgrupos* (a segunda metade de P3) — isso
    continua sendo uma fórmula própria, ainda sem endpoint `AUTO` ligado, e segue exigindo a mesma
    confirmação explícita do usuário antes de virar requisito definitivo (regra de ouro do
    CLAUDE.md) caso alguém proponha uma.
  - **Revertido parcialmente pela revisão 2026-09-02 (mais abaixo):** só para quando a própria
    alocação sendo distribuída já é SUBGROUP (Meta Supervisor/Meta Vendedor) — nesse caso o peso
    passa a ser do subgrupo específico. Pra alocação GROUP (Gerente→Regional, Regional→Local) a
    regra deste refinamento (peso sempre do grupo inteiro) continua valendo exatamente como aqui.
- **Refinamento (2026-07-22) — modo `AUTO` estendido para Gerente→Regional, a pedido explícito do
  usuário:** o repasse Gerente→Regional, até aqui só manual (Decisão original: "Gerente->Regional
  não é uma das pendências nomeadas em open-questions.md"), passa a ter `suggested_kg`
  pré-preenchido também, usando a **mesma** `SeasonalTrendDistributionStrategy` +
  `LargestRemainderRoundingPolicy` já aprovadas para P2-P4 — nenhuma fórmula nova, só registro do
  nível GERENTE no modo `AUTO` do registry (`_build_default_registry`,
  `backend/apps/allocations/strategies.py`). Segue o mesmo padrão dos demais níveis: o total de
  referência é sempre a alocação-pai (`total_kg` recebido pelo Gerente), o peso por Coordenador
  Regional vem do histórico de 12 meses do grupo inteiro (nunca subgrupo, ver refinamento
  2026-07-21 acima), o fechamento exato (sem sobra/falta) é garantido pelo método do maior resto
  (Decisão 7), e o valor sugerido continua 100% editável antes de confirmar — nada muda no
  workflow de sugestão revisável já descrito acima. Nenhuma mudança de frontend foi necessária: a
  UI (`DistributionForm`) já consumia `suggested_kg` de forma genérica e já exibia contexto
  histórico para GERENTE (ver open-questions.md, "Frente que já foi aberta").
  - **Superado pela Decisão 14 (remoção do nível Coordenador Regional):** com o nível REGIONAL
    removido da hierarquia, o repasse "Gerente→Regional" descrito acima deixa de existir como etapa
    própria — o Gerente passa a distribuir direto para o Coordenador Local (o antigo P2,
    Regional→Local). Na prática, as duas etapas descritas nesta revisão e no refinamento de P2-P4
    acima colapsam numa só: Gerente→Local, cobrindo o mesmo território de P2 sem nenhuma fórmula
    nova. Mantido aqui só como registro histórico de quando a extensão do modo `AUTO` foi pedida.
- **Revisão (2026-09-02) — âncora da janela de sincronização corrigida para "mês atual − 1" (não
  o próprio mês atual):** a fórmula em si não mudou, mas foi identificado que a janela de
  sincronização do histórico (`sync_sales_history` CLI e `SyncDataView`) usava como âncora o
  próprio dia da sincronização (`hoje`), contando "12 meses incluindo o atual" — isso deixa a
  janela **1 mês curta demais** para o cálculo sazonal de P1, que precisa do mesmo mês do ano
  anterior relativo ao mês **anterior ao ciclo** (`previous_month(cycle.ano, cycle.mes)`, ver
  `GoalSuggestionService.suggest_for_cycle`). Sintoma real: ciclo de 09/2026 aberto, mas
  `GoalSuggestionService` devolvia `seasonal_index=0.0`/`suggested_kg=0` para todos os grupos
  (`has_gap=True`) porque `DistributionBaseline` não tinha nenhuma linha de 09/2025 — a
  sincronização (rodada em 09/2026, janela de 12 meses a partir de hoje) só alcançava até
  10/2025. Corrigido ancorando a janela sempre em "mês atual − 1, e 12 meses completos pra trás
  daí" — o mês corrente (ainda em andamento) nunca entra na janela — em
  `backend/apps/sales_history/management/commands/sync_sales_history.py` e
  `backend/apps/sales_history/views.py` (`SyncDataView`), mantendo os dois pontos de entrada
  sincronizados no mesmo `months_back`. Sem essa correção, a lacuna se repetiria todo mês (não é
  um evento isolado de transição): a janela rolante de 12 meses nunca alcança o mesmo mês do ano
  anterior enquanto ancorada no dia da sincronização.
- **Revisão (2026-09-02) — peso da sugestão em alocação SUBGROUP passa a ser o histórico do
  SUBGRUPO específico, revertendo o refinamento 2026-07-21 só para esse caso, a pedido explícito
  do usuário:** quando a alocação sendo distribuída já é de granularidade SUBGROUP (telas "Meta
  Supervisor" — Local→Supervisor de um subgrupo — e "Meta Vendedor" — Supervisor→Vendedor de um
  subgrupo), `_build_child_distribution_contexts` (`backend/apps/allocations/services.py`) agora
  monta `history_by_target` com `SalesHistoryProvider.target_history(..., group_id=X,
  subgroup_id=Y)` — o subgrupo da própria alocação — em vez de só `group_id`. Isso afeta `history`,
  `same_month_last_year_kg`, `last_3_months_avg_kg`, `historical_share_pct`, `has_gap` e o peso por
  trás de `suggested_kg` igualmente, já que todos derivam do mesmo `history_by_target`.
  - **Motivo do usuário:** quem nunca vendeu aquele subgrupo específico não deveria puxar sugestão
    automática de meta nele, mesmo tendo histórico forte no grupo inteiro — a sugestão pré-preenchida
    existir "porque o grupo vende bem" para um subgrupo que a pessoa nunca tocou distorce a
    distribuição pra quem realmente atende aquele produto.
  - **Continua igual pra alocação GROUP** (Gerente→Local, o próprio P2): sem
    `subgroup_id` na alocação (é `None`), o histórico continua sendo do grupo inteiro — a fragilidade
    de dado esparso que motivou o refinamento 2026-07-21 (histórico por subgrupo é mais raro que por
    grupo) segue real e é exatamente o trade-off que o usuário decidiu aceitar aqui, cientemente.
  - **Sem histórico algum no subgrupo (nem de um alvo, nem de outro):** confirmado com o usuário
    que o comportamento correto é **não sugerir nada** (soma de peso zero → `ValueError` capturado,
    mesmo caminho já usado pra "sem `ExternalSalespersonMapping` curada") — **não** cai de volta pro
    peso do grupo inteiro. 100% manual até existir histórico real no subgrupo.
  - **Continua só a sugestão pré-preenchida, nunca uma trava:** quem está distribuindo pode digitar
    manualmente um valor pra alguém com média zero no subgrupo (ex.: lançamento de produto novo pra
    essa pessoa) — a sugestão automática zerada é só o ponto de partida, não um bloqueio.
  - Testes: `DistributionContextServiceOnSubgroupTests` em
    `backend/apps/allocations/test_subgroup_supervisor_context.py`.
- **Revisão (2026-09-03) — `RecentAverageDistributionStrategy` (média dos últimos 3 meses) vira o
  default do modo `AUTO` em todos os níveis, substituindo `SeasonalTrendDistributionStrategy`, a
  pedido explícito do usuário:**
  - **Primeiro passo (mesmo dia):** a quebra Grupo→Subgrupo (tela "Distribuir Produtos",
    `SubgroupDistributionContextService`) trocou de tendência+sazonalidade para proporção pela
    média dos últimos 3 meses de cada subgrupo — a extrapolação de tendência+sazonalidade
    amplificava demais variações do histórico (ex.: subgrupo com média de 425 kg/mês saindo
    sugerido em 82 kg). `min_share_pct=0.5`: quem fica abaixo de 0,5% de participação é zerado e
    redistribuído entre os demais, em vez de sugerir frações residuais insignificantes.
  - **Extensão (mesmo dia, achado ao investigar Revenda sob o Coordenador Local Fabiano):** vários
    subgrupos apareciam **sem nenhuma sugestão pré-preenchida para nenhum Supervisor**, mesmo com
    histórico forte e crescente ao longo do ano — ex. ALMONDEGAS, com um Supervisor vendendo até
    ~5.000 kg/mês. Causa raiz: o índice sazonal do mês-semente da janela (setembro/2025, primeiro
    mês da janela de 12 meses ao planejar setembro/2026) vem de **uma única observação** por design
    (limitação já documentada acima) — nesse mês específico, nenhum Supervisor tinha vendido nada
    daquele subgrupo, então o índice sazonal de setembro ficou em 0 e zerou a projeção inteira de
    todo mundo (`tendência × 0 = 0`), mesmo com tendência positiva forte nos outros 11 meses. Com
    todas as propostas em zero, a soma dava zero e `LargestRemainderRoundingPolicy` levantava
    `ValueError` — capturado e degradado para "sem sugestão nenhuma" (mesmo caminho de "ninguém tem
    histórico"), só que aqui havia histórico real, só não naquele mês específico.
  - **Decisão do usuário:** em vez de tratar isso como um caso especial a mais, usar a mesma
    fórmula já validada para a quebra Grupo→Subgrupo (`RecentAverageDistributionStrategy`) como
    default para **todos** os níveis do modo `AUTO` no `default_distribution_registry` — P2
    (Gerente→Local), a segunda metade de P3 (quebra Subgrupo→Supervisor) e P4
    (Supervisor→Vendedor). `SeasonalTrendDistributionStrategy` continua existindo como implementação alternativa de
    `DistributionStrategy` (plugável por design, Decisão 5) e segue coberta por testes, mas deixou
    de ser o que o registry usa.
  - **Efeito colateral aceito:** como o piso de 0,5% de `RecentAverageDistributionStrategy` some com
    a fragmentação por tendência/sazonalidade, mais alvos passam a receber uma sugestão numérica (em
    vez de ficarem sem nada quando a extrapolação zerava por instabilidade) — mais próximo da média
    real recente do que da tendência extrapolada, o que já era o motivo original da troca em
    Grupo→Subgrupo.
  - Implementado só trocando a fábrica registrada em `_build_default_registry`
    (`backend/apps/allocations/strategies.py`) — nenhuma mudança em `services.py`,
    `SalesHistoryProvider` ou nos serializers/endpoints que consomem `suggested_kg`.
- **Revisão (2026-09-03, mesmo dia) — garantia de que a sugestão automática NUNCA fica vazia,
  mesmo sem histórico algum:** a pedido explícito do usuário ("tenha certeza que sempre vai ter a
  distribuição"), `RecentAverageDistributionStrategy.distribute()` deixou de levantar `ValueError`
  quando a soma das médias de todos os alvos é zero (ninguém vendeu nada naquele
  grupo/subgrupo/janela). Em vez de degradar para "sem sugestão nenhuma" (100% manual), reparte
  `total_kg` **em partes iguais** entre os alvos — mesmo padrão que já existia para "todo mundo
  abaixo do piso de 0,5%" (linha "não cai pra partes iguais... exceto quando *todo mundo* fica de
  fora", ver docstring da classe), agora estendido para "ninguém tem histórico nenhum".
  - **Ainda é só o ponto de partida:** a sugestão pré-preenchida por partes iguais continua
    100% editável antes de confirmar — não é uma trava, só garante que sempre existe algo digitado
    de largada, mesmo pra grupo/subgrupo novo sem venda registrada.
  - **Efeito em cascata:** como isso é o único ponto de origem de todas as sugestões `AUTO` (P1
    fica de fora — segue tendência+sazonalidade, sempre pôde levantar `ValueError` genuinamente
    quando o grupo inteiro nunca vendeu nada, caso mais raro), a garantia vale automaticamente para
    P2 (Gerente→Local), P3 completo (Grupo→Subgrupo e Subgrupo→Supervisor) e P4
    (Supervisor→Vendedor) — não foi necessário mexer em `services.py` de novo.
  - Testes atualizados: os que antes esperavam `suggested_kg is None` para "sem histórico nenhum"
    agora esperam o split igualitário (`test_distribution_context.py`,
    `test_subgroup_supervisor_context.py`); teste unitário novo em
    `RecentAverageDistributionStrategyTests` (`test_strategies.py`).
- **Reversibilidade:** fácil — troca de `RoundingPolicy`/`DistributionStrategy` por design (Decisão 5).

## Decisão 7 — Método de arredondamento (P5): maior resto / Hamilton
- **Escolha:** para fechar proporções fracionárias em KG inteiro batendo exato com o total recebido,
  a fórmula aprovada é o **método do maior resto (Hamilton)**: arredonda toda proporção para baixo,
  calcula quanto sobrou (`total_kg - soma dos arredondamentos`), e distribui esse resto, 1 kg de
  cada vez, para quem tem a maior fração perdida no arredondamento — até fechar exato. Implementado
  em `LargestRemainderRoundingPolicy` (`backend/apps/allocations/strategies.py`).
- **Contexto:** era o mesmo placeholder que já existia desde o início do projeto
  (`UnapprovedLargestRemainderRoundingPolicy`), plugado só para destravar dev enquanto P5 não tinha
  aprovação. O usuário aprovou exatamente esse método ao escolher entre as opções apresentadas —
  a mudança foi só remover o rótulo de não-aprovado, sem alterar o algoritmo (já testado desde antes).
- **Alternativas descartadas:**
  - *Método dos divisores (D'Hondt):* atribui uma unidade de cada vez a quem tem o maior quociente
    (`valor / (unidades já dadas + 1)`), repetindo até fechar. Evita alguns paradoxos teóricos do
    maior resto, mas é mais complexo de explicar a um Gerente/Coordenador e exigiria implementação
    nova.
  - *Maior alvo absorve a diferença:* todo mundo arredonda, e quem tem a maior quantidade absorve
    sozinho toda a sobra/falta. Mais simples ainda, mas concentra sempre no mesmo alvo a variação de
    arredondamento, mês após mês — descartado por ser potencialmente percebido como injusto.
- **Trade-offs:** maior resto é auditável linha a linha ("faltava fechar 3 kg, foram para os 3 alvos
  com maior fração perdida") e é um método clássico e bem entendido (usado em apuração de cadeiras
  parlamentares). Em troca, tem uma limitação teórica conhecida (paradoxo de Alabama: aumentar o
  total pode, em casos raros, reduzir a parcela de um alvo específico) — aceitável dado o volume e
  a escala pequena de alvos por repasse neste produto.
- **Reversibilidade:** fácil — troca de `RoundingPolicy` por design (Decisão 5); `DistributeGoalService`
  e o `ClosureValidator` não mudam com a troca.

## Decisão 8 — Mecanismo de reabertura em cascata (H4): apagar + registrar no AuditLogEntry
- **Escolha:** ao reabrir uma alocação já distribuída, a sub-árvore de filhas invalidada é
  **apagada de verdade** (hard delete, da folha mais profunda para cima, respeitando
  `on_delete=PROTECT` em `parent_allocation`), e o evento (o que foi invalidado, quem reabriu,
  quando) é registrado no `AuditLogEntry` já existente no projeto — que passou a cobrir
  `GoalAllocation` além de `HierarchyNode`/catálogo, usando o mesmo `GenericForeignKey` (ação nova:
  `REABERTURA`). Implementado em `ReopenAllocationService`
  (`backend/apps/allocations/services.py`), exposto via `POST /api/allocations/{id}/reopen/`. Depois
  de reaberta (`distributed == False`), a mesma `DistributeGoalService.distribute()` — **sem
  nenhuma mudança nela** — refaz o repasse normalmente.
- **Alternativas descartadas:**
  - *Hard delete sem registro:* mais simples ainda (zero mudança em `audit`), mas perde qualquer
    rastro de que uma reabertura aconteceu — não combina com a ênfase do produto em auditabilidade
    (ver Invariante de auditabilidade em [architecture.md](./architecture.md)).
  - *Soft delete (marcar como obsoleta):* manteria as linhas antigas no banco (histórico mais
    completo, dá pra reconstruir a alocação inteira como estava antes), mas exigiria um campo novo
    em `GoalAllocation` (migração) e atualizar todas as queries que já filtram esse model
    (`visible_to`, `CycleCompletenessChecker.stuck_allocations`, `GoalAllocationViewSet`) para
    excluir os registros obsoletos — custo maior de mudança para um ganho de histórico que o
    `AuditLogEntry` já cobre (guarda `id`, `owner_node_id` e `quantity_kg` de cada filha invalidada
    em `changes`, suficiente para reconstruir "o que existia antes").
- **Escopo do mecanismo:** cobre só *reabrir e redistribuir* um total já existente, pelo mesmo nível
  que distribuiu (checagem de posse igual à de `DistributeGoalService`) e só com o ciclo `ABERTO`.
  Não decide a política de O4 (mudança de hierarquia com ciclo aberto) — ver
  [open-questions.md](./open-questions.md).
- **Reversibilidade:** média — trocar para soft delete depois exigiria migração + atualizar as
  queries citadas acima, mas o contrato público (`ReopenAllocationService.reopen()`,
  `POST /reopen/`) não mudaria.
- **Revisão (2026-09-03) — a trava do botão "Resetar distribuição" (`has_further_distribution`,
  2026-08-04) ignora repasses automáticos de autogestão, com ou sem quantidade:** achado real ao
  investigar por que o botão de reset sumia numa tela "Distribuição para Supervisores" mesmo com os
  dois supervisores com quantidade real (Iago, Josiel) sem terem mexido em nada ainda — o culpado
  era um terceiro supervisor (Wagner), em autogestão (repasse automático Supervisor→Vendedor,
  `SelfVendedorAutoDistributionService`), cujo repasse automático roda sempre que ele recebe algo
  (0 kg num subgrupo, 597 kg noutro) e marca esse filho como `distributed=True` — bastava isso pra
  travar o reset do pai pra todo mundo, mesmo sem nenhuma decisão real em risco.
  - **Primeira correção (só 0 kg):** `quantity_kg__gt=0` além de `distributed=True` — cobriu o caso
    do subgrupo onde o Wagner tinha 0 kg, mas não o caso seguinte, onde ele tinha 597 kg reais.
  - **Correção completa (mesmo dia, a pedido do usuário):** filho cujo `owner_node` é um Supervisor
    em autogestão (`SelfVendedorAutoDistributionService.is_self_managed_supervisor` — único
    Vendedor ativo, mesmo nome) não conta pra `has_further_distribution`, **independente da
    quantidade**. Motivo: repasse de autogestão nunca é uma decisão — só existe 1 alvo possível —,
    então resetar o pai e redistribuir de novo com outra quantidade produz o mesmo resultado
    automaticamente, sem perder nada. `self_managed_supervisor_ids()` (novo método, mesma regra em
    lote) computa isso 1x por request, não por linha, evitando N+1 na listagem.
  - **Continua bloqueando de verdade quando há quantidade real de alguém que NÃO está em
    autogestão:** o cenário original da Decisão (um Supervisor com múltiplos Vendedores já dividiu
    manualmente entre eles) não muda — só repasses de autogestão (0 kg ou não) deixaram de contar.
  - **`ReopenAllocationService.reopen()` precisou da mesma correção:** a trava de
    `blocking_children` (2026-08-04) é uma checagem *separada* de `has_further_distribution` —
    corrige só uma das duas teria deixado o botão "Resetar distribuição" aparecer na tela (porque
    `has_further_distribution` já dava `false`) mas a ação de fato falhar ao clicar (porque
    `reopen()` ainda usava a regra antiga, sem a exceção de autogestão). As duas agora aplicam
    exatamente o mesmo filtro (`quantity_kg__gt=0` + exclusão de `self_managed_supervisor_ids()`).
  - **Escopo do reset continua o mesmo de sempre (H4), não muda com esta revisão:** resetar uma
    alocação apaga em cascata SÓ a sub-árvore dela pra baixo (`_collect_descendants`) — resetar o
    repasse de um SUBGRUPO específico pra Supervisores não toca nos outros subgrupos do mesmo
    grupo (são `GoalAllocation` irmãs, independentes); resetar a alocação de GRUPO inteira (antes
    de quebrar em subgrupos) é que apaga tudo que foi feito a partir dela — subgrupos, Supervisores
    e Vendedores já cascateados — porque tudo isso é descendente dela. O botão de cada tela liga a
    um `allocation` específico (o subgrupo selecionado, ou o grupo inteiro, dependendo da tela) —
    o alcance do reset é sempre a partir *desse* nó pra baixo, nunca mais amplo que isso.
  - Testes: `test_has_further_distribution_ignores_a_zero_kg_distributed_child`,
    `test_has_further_distribution_still_blocks_on_a_real_distributed_child` e
    `test_has_further_distribution_ignores_a_self_managed_cascade_with_real_quantity` em
    `backend/apps/allocations/test_api.py`; `test_reopen_not_blocked_by_a_self_managed_cascade_even_with_real_quantity`
    em `backend/apps/allocations/test_reopen.py`.
- **Extensão (2026-09-03) — reset em lote de um grupo inteiro (`reopen_group`), pedido explícito do
  usuário:** as telas "Meta Supervisor"/"Meta Vendedor" (`SubgroupCascadeWorkspace`) só tinham o
  reset por subgrupo (`ResetDistributionButton`, ligado à alocação SUBGROUP selecionada) — tedioso
  quando quem distribui erra a distribuição do grupo inteiro pro nível de baixo (um grupo pode ter
  dezenas de subgrupos, cada um exigindo um reset separado). Ao contrário do reset do "Distribuir
  Produtos" (Grupo→Subgrupo, já existia e continua igual — reseta a alocação GROUP inteira de uma
  vez, porque ali é literalmente UMA alocação), aqui o problema era ter várias alocações SUBGROUP
  independentes, cada uma com seu próprio `distributed`.
  - **Escolha:** `ReopenAllocationService.reopen_group(*, owner_node, cycle, group_id, criado_por)`
    — encontra todas as alocações SUBGROUP daquele grupo, já distribuídas, que `owner_node` possui
    no ciclo, e reseta todas de uma vez (mesmo `_reopen_unchecked` reaproveitado por alocação).
    Exposto via `POST /api/allocations/reset-group/` (`{cycle_id, owner_node_id, group_id}`) e o
    componente `ResetGroupDistributionButton` (frontend), ao lado do resumo do grupo nas telas
    "Meta Supervisor"/"Meta Vendedor".
  - **Tudo-ou-nada (confirmado com o usuário — pergunta direta sobre bloqueio parcial):** se
    QUALQUER subgrupo do grupo tiver um filho bloqueando (mesma regra de `_blocking_children_names`
    — trabalho real, não autogestão nem 0 kg), a operação inteira falha, listando todos os nomes
    bloqueando de uma vez, e **nada** é resetado — em vez de resetar só os subgrupos livres e pular
    os travados. Evita um grupo pela metade resetado (alguns subgrupos com sugestão nova, outros
    com a distribuição antiga), mais difícil de auditar/entender do que uma falha clara.
  - **Sugestão automática pré-preenche de novo, mas sem se auto-aplicar (confirmado com o
    usuário):** depois do reset, o frontend chama `groupDraft.ensureLoaded(id, force=true)`
    (`useGroupSupervisorDraft.ts`) pra cada subgrupo resetado — recarrega a sugestão automática
    (`RecentAverageDistributionStrategy`, mesma de sempre) e pré-preenche os campos, mas a
    persistência de fato continua exigindo o mesmo "Salvar distribuição" — sugestão nunca se
    auto-aplica sozinha, mesma invariante de sempre (ver [[project_goal-suggestion-workflow]]).
    `force=true` foi necessário porque `useGroupSupervisorDraft` cacheia por `allocationId`
    (`loadedIds`) pra nunca buscar duas vezes o mesmo contexto — sem o `force`, um subgrupo que já
    tinha sido visitado ENQUANTO distribuído (`ensureContextLoaded`, sem criar rascunho) nunca
    dispararia a busca de novo depois do reset, deixando o rascunho em branco sem sugestão.
  - **Motivo do usuário:** "caso o nível hierárquico errar a distribuição ele tem que começar tudo
    novamente" — sem essa opção, corrigir um erro que afeta vários/todos os subgrupos de um grupo
    exigiria resetar um por um manualmente.
  - Testes: `ReopenGroupServiceTests` (`backend/apps/allocations/test_reopen.py`) e
    `ResetGroupApiTests` (`backend/apps/allocations/test_api.py`).
  - **Simplificação de UI (mesmo dia, a pedido do usuário):** com o reset do grupo inteiro
    disponível, o botão de reset por subgrupo (`ResetDistributionButton`) foi **removido** de
    dentro de `SupervisorDistributionWorkspace.tsx` (as telas "Meta Supervisor"/"Meta Vendedor") —
    o usuário achou que ele "só estava atrapalhando" ali, coexistindo com o botão de grupo. O
    endpoint `POST /allocations/{id}/reopen/` e o componente `ResetDistributionButton` continuam
    existindo e em uso normal nas outras telas (Distribuir Produtos, Visão Geral,
    Gerente→Regional/Regional→Local) — a remoção foi só da UI dessas duas telas específicas, nada
    mudou no backend nem no mecanismo de reset em si (H4).

## Decisão 9 — Mapeamento texto→entidade para o histórico real (O3): curadoria manual, sem casamento automático por nome
- **Escolha:** `DistributionBaseline` só tem texto solto do ERP (`subgroup_name`,
  `salesperson_name`, sem código estável) — para ligar isso às fórmulas de P1-P4
  (`SeasonalTrendSuggestionStrategy`/`SeasonalTrendDistributionStrategy`, Decisão 6), dois
  mapeamentos explícitos, **populados manualmente** (Django Admin), sem qualquer tentativa de
  casar nomes automaticamente:
  - **`ExternalProductMapping`** (já existia em `apps/catalog/models.py`, sem uso real até agora):
    `subgroup_name` → `ProductSubgroup` (o grupo vem de graça via `ProductSubgroup.group`).
  - **`ExternalSalespersonMapping`** (novo, `apps/hierarchy/models.py`): `salesperson_name` →
    `HierarchyNode`.
  - **`SalesHistoryProvider`** (`apps/sales_history/provider.py`): usa os dois mapeamentos para
    resolver `DistributionBaseline` em séries `MonthlyQuantity` — `group_history()` soma todos os
    subgrupos mapeados de um grupo (P1); `target_history()` soma o histórico de todo Vendedor
    descendente de um nó (via `ScopeResolver`/`HierarchyClosure` — cobre P2 Gerente→Local, P3
    Local→Supervisor e P4 Supervisor→Vendedor, cada um agregando na granularidade certa), com
    filtro opcional por grupo/subgrupo. Meses sem dado entram com quantidade zero — as estratégias
    exigem série mensal consecutiva, sem buracos.
- **Alternativa descartada:** casamento automático por nome (ex.: normalizar e comparar
  `salesperson_name` contra `HierarchyNode.nome`) — mais rápido de popular, mas nome livre não é
  identificador confiável (variação de formatação, homônimos, nome mudando ao longo do tempo);
  atribuir histórico de vendas ao vendedor errado seria pior do que não ter o histórico. Descartado
  por ser exatamente o tipo de heurística inventada que a regra de ouro do CLAUDE.md proíbe.
- **Trade-offs:** curadoria manual tem custo operacional (alguém precisa popular e manter os dois
  mapeamentos conforme o ERP muda), mas é auditável e não arrisca atribuição errada. Vendedores ou
  subgrupos sem mapeamento simplesmente não contribuem para o histórico (tratados como peso zero),
  não travam o cálculo nem geram erro.
- **Reversibilidade:** fácil — os mapeamentos são tabelas independentes; trocar por uma fonte de
  identificação melhor (ex.: se o ERP passar a expor código estável) não muda o contrato do
  `SalesHistoryProvider` nem das estratégias.

**Revisão 2026-07-22 — `ExternalSalespersonMapping` passa a ter uma etapa de auto-match por
igualdade EXATA.** O usuário confirmou que, nesta base, o nome cadastrado em `HierarchyNode`
(VENDEDOR) é **idêntico** (mesma grafia, sem variação) ao `DistributionBaseline.salesperson_name`
correspondente — não é mais uma heurística de similaridade/normalização (o que continua descartado
pelo motivo original), é comparação de string exata sobre duas fontes já curadas. Implementado em
`backend/apps/hierarchy/management/commands/match_external_salespersons.py`, idempotente (só cria
o que ainda não existe, nunca sobrescreve). Rodado uma vez em 2026-07-22: 90/100 Vendedores
casaram exatamente; os 10 que sobraram (grafia diferente entre o cadastro e o ERP, ou nome ainda
não sincronizado) continuam exigindo curadoria manual pelo Django Admin — o comando não tenta
aproximar esses casos. `FeristaCoverage` (Decisão 13) não é afetado: os nomes de ferista no ERP
que não têm `HierarchyNode` próprio continuam resolvidos por aquele mecanismo, não por este.

## Decisão 10 — Reatribuição de meta em mudança de hierarquia (O1, O4, O5)

**O1 — Granularidade do Vendedor: subgrupo.** A meta final do Vendedor é distribuída por subgrupo
(mesma granularidade do Supervisor, só que por pessoa) — não por produto individual. `Product`
não entra no MVP. Nenhuma mudança de código: `granularity` já era genérico e o modelo `Product`
permanece existindo, sem uso ativo, como ponto de extensão caso a decisão mude no futuro.

**O4 — Política de reatribuição: meta volta pro nó pai.**
- **Escolha:** quando um nó da hierarquia é **desativado** (`ativo` vira `False`) ou **reparentado**
  (`parent` muda) enquanto tem meta em ciclo `ABERTO`, a alocação **pai** (quem tinha distribuído
  para esse nó) é reaberta automaticamente — a mesma mecânica de H4 (Decisão 8), só que disparada
  pela mudança de hierarquia em vez de por pedido voluntário do dono. Implementado em
  `HierarchyChangeReassignmentService.reassign_open_cycle_allocations()`
  (`backend/apps/allocations/services.py`), acionado por `HierarchyNodeAdmin.save_model()`
  (`backend/apps/hierarchy/admin.py`) — único lugar onde a hierarquia é editada (Decisão 4).
  `ReopenAllocationService` ganhou um segundo ponto de entrada,
  `reopen_for_hierarchy_change()`, que reaproveita toda a lógica de cascata/audit já existente
  mas **pula a checagem de posse** (`reopen()` original exige que quem chama seja o dono da
  alocação — aqui é o Administrador mudando a hierarquia, não o dono pedindo).
- **Escopo real da reabertura:** como `distributed` é tudo-ou-nada por alocação, reabrir a
  alocação-pai invalida em cascata **toda** a sub-árvore abaixo dela — inclusive irmãos do nó
  afetado que não mudaram. Não dá pra "devolver" só a fatia do nó afetado sem redistribuir o total
  de novo; quem redistribui (o dono do nó pai) decide para onde vai cada parte, inclusive a do nó
  removido/movido.
- **Alternativas descartadas:**
  - *Reatribuir a um substituto indicado:* exigiria que quem muda a hierarquia já soubesse, no
    mesmo ato, quem herda a meta — acopla uma decisão de negócio (quem substitui) a uma operação de
    cadastro; descartado em favor de deixar essa escolha para quem redistribui depois.
  - *Zerar a meta do nó removido:* perderia KG da meta global sem motivo — violaria o espírito da
    invariante de fechamento (nada deveria desaparecer sem ser redistribuído).
  - *Bloquear mudança de hierarquia com ciclo aberto:* mais simples (nenhuma política de
    reatribuição seria necessária), mas o usuário preferiu permitir a mudança e resolver via
    reatribuição ao pai.
- **Gatilho técnico:** `HierarchyNodeAdmin.save_model()` compara o estado anterior do nó (buscado
  do banco antes de salvar) com o novo, e dispara a reatribuição só quando `ativo` vai de
  `True`→`False` ou `parent_id` muda — criação de nó novo ou edição de outros campos (nome, etc.)
  não dispara nada.

**O5 — Cardinalidade User↔Node: 1:N.**
- **Escolha:** uma pessoa pode ocupar mais de uma posição/ramo ao mesmo tempo. `User.hierarchy_node`
  (antes `OneToOneField`) virou `User.hierarchy_nodes` (`ManyToManyField` para `HierarchyNode`,
  `related_name="users"`). Isolamento de escopo (`HierarchyNode.objects.visible_to`,
  `GoalAllocation.objects.visible_to`) e checagens de posse (`DistributeGoalService`,
  `ReopenAllocationService`) passaram a **unir** os nós do usuário em vez de comparar contra um
  único `hierarchy_node_id`. `ScopeResolver.descendant_ids()` (`apps/hierarchy/services.py`) foi
  generalizado para aceitar um id **ou** uma lista de ids, mantendo compatibilidade com quem já
  chamava com um único id (ex.: `SalesHistoryProvider`).
- **Conveniência de criação:** `UserManager.create_user()`/`create_superuser()` (custom, em
  `apps/accounts/models.py`) aceitam `hierarchy_node=` (singular) como atalho pro caso comum de um
  usuário com um nó só — internamente vira `user.hierarchy_nodes.add(node)`. Isso evitou reescrever
  as ~30 chamadas de teste existentes que já usavam esse padrão; a atribuição de mais de um nó usa
  `user.hierarchy_nodes.add(node_a, node_b)` diretamente (testado em `MultiNodeUserScopeTests`,
  `apps/allocations/test_isolation.py`).
- **Migração:** `accounts.0002_...` remove o campo antigo e adiciona o M2M — sem migração de dados
  (o vínculo 1:1 existente, se houvesse, seria perdido). Aceitável: banco de desenvolvimento local,
  sem dado de produção ainda.
- **Alternativa descartada:** manter `hierarchy_node` (1:1) como "nó principal" ao lado de um novo
  `hierarchy_nodes` (M2M) para não quebrar nada — descartado por duplicar a fonte de verdade
  (qual dos dois vale?) e ser exatamente o tipo de shim de compatibilidade que o CLAUDE.md pede pra
  evitar quando dá pra só mudar o código.
- **Trade-off aceito:** com 1:N, uma alocação distribuída por "Fulano" não identifica mais
  sozinha *qual* das posições de Fulano a criou — só que ele tinha posse de alguma delas no
  momento. Não é um problema hoje (nenhuma tela/relatório depende disso), mas vale revisitar se
  isso passar a importar (ex.: auditoria por posição, não só por pessoa).
- **Reversibilidade:** média — voltar pra 1:1 exigiria decidir qual nó "vence" para usuários que
  hoje tenham mais de um.

- **Revisão (2026-07-21) — múltiplos cargos passam a ser alcançáveis pela API/SPA, não só pelo
  Django Admin:** o schema M2M (1:N) já existia desde a Decisão 10 original, mas
  `UserAccountSerializer._sync_position` só tratava **uma** posição por usuário (a primeira) —
  criar/editar pela tela sempre criava, reaproveitava ou reparentava esse único nó, sem jeito de
  *acrescentar* um segundo cargo sem antes passar pelo Django Admin diretamente no banco.
  - **Escolha:** duas actions novas em `UserAccountViewSet`
    (`backend/apps/accounts/views.py`): `POST /accounts/users/{id}/positions/` (acrescenta um
    cargo novo — mesma resolução de nó da Decisão 11: reaproveita um nó livre com nome/cargo/
    superior batendo, ou cria um novo) e `DELETE /accounts/users/{id}/positions/{node_id}/`
    (desvincula um dos cargos, sem apagar o nó). Nenhuma das duas mexe nas posições que a pessoa
    já tem. A lógica de resolução de nó foi extraída pra uma função module-level
    (`resolve_or_create_node`, `apps/accounts/serializers.py`), compartilhada entre o fluxo de
    posição inicial (`create`/`update`) e essas duas actions novas.
  - **Caso de uso concreto:** um Gerente que também acumula o cargo de Coordenador
    Local de um dos ramos abaixo dele (ou um Coordenador Local que também é Supervisor de um dos
    seus próprios Supervisores) — a mesma pessoa, dois nós na árvore, cada um com seu próprio cargo
    e superior.
  - **Sem tela própria ainda:** essas duas actions são só backend — não há botão em
    `UserEditModal`/`HierarchyManager` pra usá-las por enquanto (ficaria pra uma iteração futura
    de UI, se pedido). Testadas em `test_admin_can_add_second_position_to_same_user`,
    `test_add_position_rejects_exact_duplicate` e
    `test_admin_can_remove_one_of_multiple_positions_without_deleting_the_node`
    (`backend/apps/accounts/test_api.py`).
  - **Reversibilidade:** fácil — são duas actions aditivas; removê-las não afeta o fluxo de
    posição única (`create`/`update`), que continua intacto.

## Decisão 11 — Todo usuário criado já nasce vinculado à posição real na hierarquia
- **Regra operacional:** ao cadastrar um usuário (tela Gestão → Usuários, `UserAccountSerializer`),
  o cargo/posição na hierarquia é definido **no mesmo ato de criação** — não existe fluxo aprovado
  de "criar usuário sem hierarquia e vincular depois", exceto para o papel Administrador puro (sem
  posição na cascata, Decisão 4).
- **Por quê:** um usuário sem nó vinculado não consegue receber/distribuir meta (toda a lógica de
  posse e escopo em `DistributeGoalService`/`ReopenAllocationService`/`ScopeResolver` depende de
  `user.hierarchy_nodes`) — deixar esse vínculo para "depois" só cria trabalho de garimpo futuro
  (usuários órfãos) sem nenhum benefício.
- **Como o produto favorece isso:** o formulário de usuário (`UserEditModal`) tem como campo
  principal "Posição na hierarquia" — um seletor único com todas as posições já cadastradas e
  ainda sem usuário, agrupadas por cargo. Reaproveitar uma posição existente (ex.: hierarquia
  importada de planilha, com o nome real da pessoa) é o caminho padrão; criar cargo+superior do
  zero (`node_id` ausente na API) é a exceção, reservada para gente que ainda não tem nó
  cadastrado. Isso evita o problema anterior de cada criação gerar um nó novo nomeado pelo
  username em vez de reaproveitar o nome real já existente na árvore.
- **Para quem estende esta tela (humano ou agente de IA):** qualquer fluxo novo de criação de
  usuário (import em lote, endpoint novo, etc.) deve seguir a mesma regra — resolver a posição na
  hierarquia (`level` + `parent_node_id`, ou reaproveitando um nó existente via `node_id`) como
  parte do mesmo passo, nunca como etapa manual separada depois do fato.
- **Reversibilidade:** alta — é uma convenção de uso da tela, não uma restrição de schema (o campo
  `hierarchy_nodes` sempre aceitou ficar vazio, ex.: Administrador).

- **Revisão (2026-07-21) — seletor manual de "Posição na hierarquia" removido; resolução
  automática por cargo+superior+nome:** o `UserEditModal` (Usuários e o lápis da tela
  Hierarquia) tinha um seletor de nó existente + botão "criar posição nova", que só listava nós
  **sem usuário** — não dava pra trocar quem ocupa uma posição já vinculada (ex.: substituir o
  titular de um cargo por outra pessoa), porque o nó antigo nunca aparecia na lista pra ninguém
  além do próprio ocupante.
  - **Escolha:** o formulário passou a pedir só **Nome completo**, **Cargo** e **Superior
    imediato** — sempre visíveis, sem alternância de modo. `username` (campo técnico do Django,
    login continua sendo por e-mail) virou literalmente o **nome completo da pessoa**, sem
    transformação (sem slug, sem pontuação inserida) — o validador padrão
    (`UnicodeUsernameValidator`, que rejeita espaço) foi trocado por um mínimo que só barra
    número (migração `accounts.0005_alter_user_username`).
  - **Resolução do nó, no backend (`UserAccountSerializer._sync_position`):**
    - **Usuário sem posição ainda:** procura um `HierarchyNode` existente, **sem usuário**, com
      `nome` batendo (case-insensitive) + mesmo cargo + mesmo superior; se achar, reaproveita
      (continua o espírito da Decisão 11 original — evitar nó duplicado da hierarquia importada
      de planilha); senão, cria um novo.
    - **Usuário já tem posição:** editar cargo/superior **reparenta o mesmo nó** (nunca cria
      outro) — e o `nome` do nó é sempre resincronizado com o `username` atual, a cada salvamento.
  - **Como isso resolve a "troca de titular":** substituir quem ocupa uma posição (ex.: Alexandre
    → Marcelo Rodrigues Cireli num Coordenador Local) virou só **editar nome completo/login da
    pessoa**, sem tocar em cargo/superior — não é mais uma operação de hierarquia.
  - **Alternativa descartada:** manter o seletor de nó, só liberando nós ocupados por outros
    (via alguma flag "trocar titular") — mais explícito, mas o usuário achou o fluxo de dois
    passos (Cargo+Superior *ou* escolher nó existente) confuso demais no dia a dia; preferiu que
    o sistema resolva isso sozinho.
  - **Trade-off aceito:** duas pessoas homônimas (mesmo nome completo) no mesmo cargo/superior
    fariam a segunda criação **reaproveitar por engano** a posição da primeira, se a primeira
    ficasse sem usuário justo nessa janela — cenário improvável na escala desta ferramenta
    (dezenas de posições, não milhares), aceito sem mitigação adicional.
  - **Reversibilidade:** média — o campo `node_id` foi removido da API (`UserAccountSerializer`,
    `UserAccountInput`); voltar ao seletor manual exigiria reintroduzir o campo e a tela, mas o
    dado (`HierarchyNode`/`User.hierarchy_nodes`) não muda de formato.

---

## Decisão 12 — Vendas sem vendedor vigente entram em `DistributionBaseline` (não ficam de fora) e o total agrupado é sempre KG inteiro
- **Contexto:** `DistributionBaselineService.rebuild()` reatribui cada venda do acumulado ao
  vendedor **atual** da carteira do cliente (join por `client_code`). Até 2026-07, clientes do
  acumulado sem entrada correspondente na carteira atual eram descartados (`continue`) — o volume
  desses clientes desaparecia da base inteira, inclusive da soma que alimenta a sugestão de meta
  do Gerente (P1).
- **Escolha (confirmada pelo usuário, 2026-07):** essas vendas passam a gerar uma linha própria em
  `DistributionBaseline` com `salesperson_name=NULL`, em vez de sumir.
  - **P2-P4** (Vendedor/Supervisor/Coordenador) continuam sem enxergar esse volume — `SalesHistoryProvider.target_history`
    filtra por `salesperson_name__in=[...]` explícitos, e `NULL` nunca casa com um nome específico
    (correto: não há vendedor vigente pra atribuir individualmente).
  - **P1** (sugestão para o Gerente) passa a incluir esse volume — `SalesHistoryProvider.group_history`
    soma por `subgroup_name` sem filtrar por vendedor, então linhas `NULL` entram na conta. Isso é
    o objetivo da mudança: o Gerente vê o volume real do grupo, mesmo a fração momentaneamente sem
    vendedor titular na carteira.
- **Arredondamento:** o valor agrupado (`total_quantity`) é sempre inteiro — regra confirmada pelo
  usuário: `< 0,5` arredonda para baixo, `>= 0,5` arredonda para cima (`ROUND_HALF_UP`, aplicado em
  `Decimal.quantize` antes de persistir). Não é o método do maior resto / Hamilton (P5, Decisão 7)
  — aquele fecha um repasse hierárquico contra um total recebido; este só transforma a soma
  histórica bruta em KG inteiro, sem nenhum total-alvo pra fechar contra.
- **Schema:** `salesperson_name` passou a `null=True, blank=True`; `total_quantity` passou de
  `decimal_places=6` para `decimal_places=0` (migração `0004_alter_distributionbaseline_salesperson_name_and_more`).
- **Reversibilidade:** média — reverter exige popular `salesperson_name` de volta com um valor não
  nulo (ou filtrar essas linhas na leitura) e desfazer a migração de `decimal_places`; os dados de
  `AccumulatedSale`/`ClientPortfolioSnapshot` na origem não são afetados, então um novo `rebuild()`
  reconstrói a partir deles em qualquer direção.

---

## Decisão 13 — Cobertura de férias (`FeristaCoverage`): redireciona histórico do ferista pro titular coberto, por mês
- **Contexto:** ao curar `ExternalSalespersonMapping` (Decisão 9/O3) com dados reais, sobraram
  nomes de `salesperson_name` no histórico sincronizado sem par exato entre os Vendedores da
  hierarquia. O usuário identificou (2026-07-22): são vendedores "feristas" — cobrem férias de um
  titular por um período, vendem em nome próprio no ERP, e não recebem meta própria. Sem vínculo
  nenhum, esse volume simplesmente desaparecia do histórico por nó (`target_history`, usado em
  P2-P4 e no contexto histórico da tela de distribuição) durante o mês da cobertura.
- **Opção descartada:** dar ao ferista um nó `HierarchyNode` próprio (nível VENDEDOR). Esbarra na
  validação de níveis fixos (`HierarchyNode.clean()`/`HierarchyNodeSerializer.validate` — pai
  sempre exatamente um nível acima) se ligado direto ao Coordenador Local, e mesmo ligado a um
  Supervisor normal ele apareceria como alvo de distribuição de meta na tela de Supervisor →
  Vendedor, o que é errado (ferista não recebe meta).
- **Escolha (confirmada pelo usuário, 2026-07-22):** modelo novo `FeristaCoverage`
  (`apps/hierarchy/models.py`) — **não é um nó da hierarquia**, só liga um `external_name` (nome
  livre do ERP, igual `ExternalSalespersonMapping.external_name`) a um `covered_node` (o
  `HierarchyNode` VENDEDOR titular, já existente na árvore) por `ano`/`mes`.
  - **Granularidade mensal, não data exata:** `DistributionBaseline` (fonte do histórico) só
    existe por mês — precisão de dia seria falsa, já que não dá pra fatiar um mês de venda entre
    dois titulares.
  - **Histórico vem de graça:** cada linha é um período; trocar a cobertura é só cadastrar uma
    linha nova pro mês seguinte, nada é sobrescrito.
  - `UniqueConstraint(external_name, ano, mes)`: um ferista só cobre uma pessoa por mês.
    `covered_node.level` precisa ser VENDEDOR (validado em `clean()` e no serializer).
- **Cálculo:** `SalesHistoryProvider.target_history` (`apps/sales_history/provider.py`) passou a,
  mês a mês, somar ao histórico normal (via `ExternalSalespersonMapping`) o volume de
  `DistributionBaseline` vendido pelo `external_name` do ferista **só nos meses em que ele cobriu
  aquele nó** — fora disso, o nome do ferista não conta pra ninguém, igual qualquer nome sem
  mapeamento. `group_history` (P1, soma por subgrupo sem filtrar vendedor) já contava esse volume
  de qualquer forma, sem mudança.
- **Interface:** função nova do Administrador — aba "Feristas" em Gestão
  (`frontend/src/pages/admin/FeristaManager.tsx`), CRUD via
  `GET/POST/PATCH/DELETE /api/hierarchy/ferista-coverages/` (`FeristaCoverageViewSet`, leitura para
  qualquer autenticado, escrita só `IsAppAdmin` — mesmo padrão de catálogo/hierarquia). Também
  registrado no Django Admin como caminho alternativo.
- **Reversibilidade:** alta — `FeristaCoverage` não é referenciada por mais nada (diferente de
  `HierarchyNode`/`ExternalSalespersonMapping`, protegidos por `on_delete=PROTECT` em cascata);
  apagar uma linha errada não deixa órfão nenhum.
- **Limitação conhecida e aceita (confirmada pelo usuário, 2026-07-22):** `DistributionBaselineService.rebuild()`
  (Decisão 9/12) reatribui **todo** o histórico de um cliente ao dono ATUAL dele na carteira — não
  só o mês da venda. Se a última sincronização caiu durante a cobertura, o ferista aparece como
  dono atual dos clientes que está cobrindo, e o histórico inteiro desses clientes (não só o mês
  coberto) cai sob o nome dele em `DistributionBaseline`. `FeristaCoverage` só redireciona o(s)
  mês(es) explicitamente cadastrado(s) — os demais meses desse volume ficam **fora** de qualquer
  histórico (nem titular, nem ferista) até uma sincronização futura, quando a carteira (já
  revertida pro titular real) reatribui esses meses de volta sozinha, sem precisar de
  `FeristaCoverage` nenhum. Decisão explícita: não tentar adivinhar o titular dos meses sem
  cobertura cadastrada, mesmo quando o ferista só tem um titular coberto — só o que está
  cadastrado mês a mês vale.

**Revisão 2026-08-28 — redirecionamento do mês corrente passa a acontecer dentro do próprio
`rebuild()`, não só em `target_history`.** O usuário identificou que o redirecionamento (Decisão
13 original) só existia em `SalesHistoryProvider.target_history`, consumido pela sugestão
automática (P1-P4) e pela distribuição — mas telas que leem `DistributionBaseline` direto (ex.:
`VendorGroupSummaryService`, tabela "Resumo por vendedor e grupo" em Gestão → Pré-processamento)
nunca aplicavam esse redirecionamento, então o volume do ferista simplesmente não somava em
lugar nenhum ali (o ferista não tem `ExternalSalespersonMapping`, então nem aparece como linha
própria). **Escolha:** `DistributionBaselineService.rebuild(today=...)` agora resolve, pra cada
`FeristaCoverage` cujo `ano`/`mes` bate com o mês/ano do `today` da reconstrução (parâmetro
opcional, default `date.today()`), o `external_name` cadastrado como `ExternalSalespersonMapping`
do `covered_node`, e regrava as linhas daquele mês específico direto com o nome do titular — não
com o nome do ferista. Meses diferentes do atual cadastrados em `FeristaCoverage` continuam **fora**
do escopo deste redirecionamento (mesma limitação aceita acima, inalterada): só o mês da
reconstrução é redirecionado aqui, o resto segue dependendo do redirecionamento mês a mês de
`target_history`. Como as linhas do mês atual já saem daqui com o nome do titular,
`target_history` não encontra mais linhas do ferista pra esse mês específico e não soma nada em
dobro.

---

## Decisão 14 — Remoção do nível Coordenador Regional: hierarquia passa de 5 para 4 níveis
- **Contexto:** o produto foi originalmente desenhado (Problem Brief, ver
  [docs/kickoff/01-problem-brief.md](./kickoff/01-problem-brief.md)) para a hierarquia de 5 níveis
  da empresa de referência inicial: **Gerente → Coordenador Regional → Coordenador Local →
  Supervisor → Vendedor**. Ao adaptar a aplicação para uma nova empresa, o usuário confirmou
  (2026-07-24) que a estrutura organizacional real tem só **4 níveis**: **Gerente → Coordenador
  Local → Supervisor → Vendedor** — não existe o nível "Regional" nessa empresa.
- **Escolha:** remover o nível `REGIONAL` do enum `HierarchyNode.level` e de toda a cascata de
  distribuição. O Gerente passa a distribuir a meta (granularidade GROUP) direto para os
  Coordenadores Locais, que continuam quebrando GROUP em SUBGROUP para os Supervisores (P3), que
  continuam distribuindo SUBGROUP entre os Vendedores (P4) — nenhuma outra etapa da cascata muda.
- **Impacto nas 5 pendências de cálculo (P1-P5, Decisões 6 e 7):** nenhuma fórmula nova foi
  inventada. O que antes era **duas** etapas distintas — Gerente→Regional (extensão do modo `AUTO`
  pedida em 2026-07-22, ver Decisão 6) e P2 Regional→Local — colapsa numa etapa só, **P2
  Gerente→Local**, reaproveitando a mesma `SeasonalTrendDistributionStrategy` +
  `LargestRemainderRoundingPolicy` já aprovadas (peso pelo histórico do grupo inteiro do alvo,
  nunca subgrupo — mesmo refinamento de 2026-07-21). P1 (sugestão ao Gerente), P3 (quebra
  Local→Supervisor), P4 (Supervisor→Vendedor) e P5 (arredondamento) ficam **inalterados** — não
  dependiam do nível Regional.
- **Documentação atualizada em consequência:** [PROJECT.md](./PROJECT.md),
  [architecture.md](./architecture.md), [data-model.md](./data-model.md),
  [open-questions.md](./open-questions.md), [roadmap.md](./roadmap.md) e os documentos de kickoff
  ([01-problem-brief.md](./kickoff/01-problem-brief.md),
  [02-solution-design.md](./kickoff/02-solution-design.md)) — todas as referências à cascata de 5
  níveis/ao nível `REGIONAL` foram revistas para refletir os 4 níveis atuais. Entradas anteriores
  deste arquivo (Decisões 1-13) que mencionam "Coordenador Regional"/"Regional→Local" em narrativa
  histórica **não foram reescritas** — descrevem decisões tomadas quando a hierarquia ainda tinha 5
  níveis; onde a menção era uma afirmação de estado atual (enum, nível do registry `AUTO`, exemplos
  ilustrativos), foi corrigida para os 4 níveis vigentes.
- **Fora do escopo desta decisão:** a mudança de código (models, migração, enums, estratégias,
  frontend, testes) ainda não foi feita — esta decisão registra só a mudança de requisito de
  hierarquia e a atualização da documentação. Ver [open-questions.md](./open-questions.md) para o
  levantamento dos pontos de código que passam a exigir essa migração.
- **Reversibilidade:** média — reintroduzir o nível Regional exigiria migração de schema
  (`HierarchyNode.level`), ajuste do registry de estratégias e reconstrução da etapa
  Regional→Local separada de Gerente→Local; nenhuma fórmula muda, só a topologia da árvore.

---

### Ressalva de rastreabilidade (transparência)
A preferência do usuário por **Python** (backend + testes) e as **prioridades de frontend** (setup
fácil + UI atraente) foram declaradas **após** a 1ª versão do design. A decisão de stack **também se
sustenta por fit técnico independente** dessas preferências — elas confirmam e reforçam a Decisão 1,
não a originaram. Registrado para honestidade de histórico, não como pendência.

Open questions relacionadas (O1–O5) e pendências de cálculo: ver [open-questions.md](./open-questions.md).
