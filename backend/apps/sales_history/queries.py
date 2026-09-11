"""SQL verbatim fornecido pelo usuário para o Postgres externo (schema `stage`/`stage_comercial`).

Regra de negócio do ERP, não reinterpretada nem simplificada aqui — a única mudança em relação
ao original é trocar a data mínima fixa do acumulado por um parâmetro (`%s`), para a janela
poder ser configurada pelo management command em vez de ficar hardcoded.

`CARTEIRA_SQL` recebeu uma correção do usuário (2026-07): o recorte de estado usava
`ds_estado` (nome por extenso), incompatível com o valor real da coluna e descartando clientes
ativos da carteira; trocado por `sg_estado` (sigla). O supervisor B.F.212 também vende em SP e GO
além de MS, e o B.F.292 vende em MS — nenhum dos dois entrava no filtro original, então ambos
foram adicionados explicitamente.

`stage.st_vendedor.nk_vendedor` não é único globalmente — o mesmo código pode pertencer a dois
vendedores reais distintos (CPF diferente) cadastrados em empresas (`nk_empresa`) diferentes. Nos
casos encontrados, o cadastro "fantasma" duplicado sempre caía em São Paulo, enquanto o vendedor
real de cada código estava em GO/MS/MT. Adicionado `vendedor.ds_estado NOT LIKE '%SAO PAULO%'`
(mesmo filtro que `ACUMULADO_SQL` já usava em `vend.ds_estado`, agora espelhado aqui) pra excluir
esse cadastro fantasma do JOIN da carteira.

Dois supervisores novos, `B.F.290` e `B.F.214` (2026-08-07, correção do usuário): adicionados à
lista de `nk_supervisor` das três CTEs `sup_map_*` de `ACUMULADO_SQL` (histórico dessa lista;
`CARTEIRA_SQL` não usa mais esse filtro, ver abaixo). Ver `SalesHistorySqlSyntaxTests` em
`tests.py` — dois literais de string colados sem vírgula entre eles (`'B.F.253''B.F.290'`) formam
UM literal só em Postgres (`''` escapa uma aspa dentro da string), não dois separados; não dá erro
visível na hora de editar, só quando o sync roda contra o banco real.

`CARTEIRA_SQL` substituída por completo (2026-09-10, nova versão fornecida pelo usuário): o
recorte anterior (empresa `B.F.`, supervisores/estados GO/MS/SP/MT) era da Bello — a Levo opera
sob o prefixo de empresa `L.F.` no mesmo schema (`stage.st_vendedor.nk_vendedor LIKE 'L.F%'`),
sem filtro de supervisor/estado. A nova versão também não seleciona mais `nk_supervisor` (por
isso o campo saiu de `ClientPortfolioSnapshot`) e troca a lista de exclusão de vendedores/CNPJs
específicos da Bello por uma lista própria da Levo (um vendedor por nome, cinco CNPJs por
`nr_cgccpf`). Perdeu, em relação à versão anterior, `clifor.dt_venctocad IS NULL` e o filtro
anti-fantasma `vendedor.ds_estado NOT LIKE '%SAO PAULO%'` — confirmado com o usuário que a query
nova é pra entrar verbatim, sem repor esses dois filtros.

`ACUMULADO_SQL` substituída por completo (2026-09-10, mesma rodada, nova versão fornecida pelo
usuário): mesmo motivo — a versão anterior filtrava por `nk_supervisor` da Bello (`B.F.xxx`) via
três CTEs `sup_map_*`; a nova filtra a Levo direto pelo JOIN
`vendin.nk_empresa = ('L.F.' || sup.cd_emprvend)`, com duas exclusões pontuais de supervisor
(`L.F.34`, `L.F.184`) em vez da lista antiga. Duas adaptações mecânicas na hora de colar (não são
regra de negócio nova, só preservar o que já existia): a data mínima do acumulado, que a versão
do usuário trazia fixa (`DATE '2026-05-01'`, o valor que ela estava usando pra testar contra o
banco real), voltou a ser o parâmetro `%s` (mesmo motivo do parágrafo acima: `sync_accumulated`
já chama isso com `[min_date]`, e a janela de 12 meses configurável é H2/Decisão 6 — sem o `%s`
o sync quebra com erro de parâmetro antes mesmo de rodar). A query também ganhou duas colunas
novas no resultado (`cidade_supervisor`, `ds_item`) que `SalesHistorySyncService.sync_accumulated`
ainda não consome — ficam disponíveis no dict de cada linha pra uso futuro, sem exigir mudança de
model agora. `cli.cd_clifor` deixou de ganhar o apelido `AS clifor` (sai como `cd_clifor` mesmo),
então `sync_accumulated` foi ajustado pra ler `row["cd_clifor"]` em vez de `row["clifor"]`.
"""

ACUMULADO_SQL = """
SELECT DISTINCT
    nk_supervisor,
    nk_vendedor,
    cidade_supervisor,
    nome_vendedor,
    cd_clifor,
    cnpj,
    nome_cliente,
    dt_emissao,
    ds_subgrupo,
    ds_item,
    total_ps_atendido,
    total_vl_movtocontabil
FROM (
    SELECT
        sup.nk_supervisor,
        vendin.nk_vendedor,
        vend.nm_clifor AS nome_vendedor,

        cli.cd_clifor,
        cli.nr_cgccpf   AS cnpj,
        cli.nm_fantasia AS nome_cliente,

        CASE emp.nk_empresa
           WHEN 'L.F.3'  THEN 'IPORÃ'
           WHEN 'L.F.7'  THEN 'BRASÍLIA'
           WHEN 'L.F.8'  THEN 'COLOMBO'
           WHEN 'L.F.22' THEN 'APUCARANA'
           WHEN 'L.F.24' THEN 'MARIPÁ'
           WHEN 'L.F.5'  THEN 'CAPANEMA'
           WHEN 'L.F.1'  THEN 'UMUARAMA'
        END AS cidade_supervisor,

        CAST(vendin.dt_emissao AS DATE) AS dt_emissao,

        item.ds_subgrupo,
        item.ds_item,

        SUM(vendin.qt_nota)          AS total_ps_atendido,
        SUM(vendin.vl_tot_item_cont) AS total_vl_movtocontabil

    FROM stage_comercial.st_venda_dinamica vendin
    INNER JOIN stage.st_vendedor vend
        ON vendin.nk_vendedor = vend.nk_vendedor
    INNER JOIN stage.st_item item
        ON vendin.nk_item = item.nk_item

    INNER JOIN stage.st_supervisorvenda sup
        ON vendin.nk_vendedor = sup.nk_vendedor
       AND vendin.nk_empresa   = ('L.F.' || sup.cd_emprvend)
       AND sup.dt_cancelamento IS NULL

    LEFT JOIN stage.st_cliforendereco cfend
        ON vendin.nk_cliforendereco = cfend.nk_cliforendereco
    LEFT JOIN stage.st_clifor cli
        ON cfend.nk_clifor = cli.nk_clifor

    INNER JOIN stage.st_empresa emp
        ON vendin.nk_empresa = emp.nk_empresa

    WHERE
        vendin.dt_emissao >= %s
        AND vend.nm_clifor <> 'EDILSON TIAGO LAZARO BARBIERI'
        AND sup.nk_supervisor <> 'L.F.34'
AND sup.nk_supervisor <> 'L.F.184'
AND vendin.cd_movimentacao <> 69
        AND (
            CASE
                WHEN vendin.cd_movimentacao IN (64,101,164,864,6550,6551,7153) THEN 'N'
                WHEN vendin.nk_empresa IN ('L.F.1','L.F.5')
                     AND vendin.cd_movimentacao IN (3138,6913) THEN 'S'
                WHEN vendin.nk_empresa LIKE 'L.F.1'
                     AND vendin.cd_movimentacao IN (599,796) THEN 'S'
                WHEN vendin.nk_empresa IN ('L.F.3','L.F.8','L.F.22','L.F.24','L.F.5','L.F.27')
                     AND vendin.cd_movimentacao = 796 THEN 'S'
                WHEN vendin.nk_empresa LIKE 'L.F.6'
                     AND item.cd_subgrupo IN (300,301,302,303) THEN 'S'
                WHEN vendin.nk_empresa LIKE 'L.F.7'
                     AND vendin.cd_movimentacao IN (796,1770) THEN 'S'
                WHEN vendin.nk_empresa LIKE 'L.F.9'
                     AND vendin.cd_movimentacao IN (796,1770,2884,9075) THEN 'S'
                WHEN vendin.cd_movimentacao IN (796,7008)
                     AND vendin.nk_item IN (
                        'L.F.566233', 'L.F.588411', 'L.F.566241', 'L.F.566258', 'L.F.566266'
                     ) THEN 'N'
                WHEN vendin.nk_empresa LIKE 'L.F.50'
                     AND vendin.cd_movimentacao IN (7075,7770,9075,796,7623) THEN 'S'
                WHEN vendin.nk_item IN (
                    'L.F.566233', 'L.F.588411', 'L.F.566241', 'L.F.566258', 'L.F.566266'
                ) THEN 'N'
                WHEN vendin.cd_movimentacao IN (
                    40,69,1770,1771,1772,1776,1777,1778,
                    3026,4040,6003,6006,6007,6008,6010,6011,
                    6910,6911,7002,7003,7005,7006,7008,7009,7015,
                    7033,7075,7570,7706,7707,7710,7711,7745,
                    7780,7806,8002,8003,8008,8040,8043,8075,8076,
                    6707,6708,3138,559
                ) THEN 'S'
                ELSE 'N'
            END
        ) = 'S'

    GROUP BY
        sup.nk_supervisor,
        vendin.nk_vendedor,
        vend.nm_clifor,
        cli.cd_clifor,
        cli.nr_cgccpf,
        cli.nm_fantasia,
        item.ds_subgrupo,
        item.ds_item,
        CAST(vendin.dt_emissao AS DATE),
        emp.nk_empresa
) sub
ORDER BY
    dt_emissao, nk_supervisor, ds_subgrupo, ds_item;
"""

CARTEIRA_SQL = """
WITH base AS (
    SELECT
        clifor.cd_clifor          AS Clifor,
        clifor.nr_cgccpf          AS CNPJ,
        clifor.nm_clifor          AS Nome_Cliente,
        vendedor.nm_clifor        AS Nome_Vendedor,
        endereco.ds_municipioibge AS Municipio,
        endereco.ds_estado        AS Estado,
        clifor.dt_cadastramento   AS Cadastro,
        clifor.dt_ultaltecadastro AS Alterado
    FROM stage.st_cliforendereco AS endereco
    JOIN stage.st_clifor AS clifor
        ON endereco.nk_clifor = clifor.nk_clifor
    JOIN stage.st_vendedor AS vendedor
        ON endereco.nk_vendedor = vendedor.nk_vendedor
    WHERE endereco.st_ativo = 'S'
     AND clifor.ds_tp_clifor ILIKE 'Cliente'
      AND vendedor.nk_vendedor LIKE 'L.F%%'
      AND vendedor.nm_clifor <> 'EDILSON TIAGO LAZARO BARBIERI'
AND clifor.nr_cgccpf NOT IN (
    '04223316000284',
    '04319655000188',
    '23179956000106',
    '54300832000829',
    '73710444000194'
)
)

SELECT DISTINCT ON (Nome_Vendedor, CNPJ)
    Clifor,
    CNPJ,
    Nome_Cliente,
    Nome_Vendedor,
    Municipio,
    Estado,
    Cadastro,
    Alterado
FROM base
ORDER BY
    Nome_Vendedor,
    CNPJ,
    Alterado,
    Cadastro;
"""
