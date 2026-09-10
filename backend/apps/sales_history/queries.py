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
lista de `nk_supervisor` das três CTEs `sup_map_*` de `ACUMULADO_SQL` e ao `IN` de MS de
`CARTEIRA_SQL` (`B.F.214` já estava nesse `IN`; só `B.F.290` é novo ali). Ver
`SalesHistorySqlSyntaxTests` em `tests.py` — dois literais de string colados sem vírgula entre
eles (`'B.F.253''B.F.290'`) formam UM literal só em Postgres (`''` escapa uma aspa dentro da
string), não dois separados; não dá erro visível na hora de editar, só quando o sync roda contra
o banco real.
"""

ACUMULADO_SQL = """
WITH sup_map_geral AS (
    SELECT
        sv.nk_vendedor,
        ('B.F.' || sv.cd_emprvend) AS nk_empresa,
        MIN(sv.nk_supervisor) AS nk_supervisor
    FROM stage.st_supervisorvenda sv
    WHERE sv.dt_cancelamento IS NULL
      AND sv.nk_supervisor IN (
            'B.F.434','B.F.292','B.F.80','B.F.229',
            'B.F.212','B.F.293','B.F.446','B.F.1017','B.F.253','B.F.290','B.F.214'
      )
      AND sv.nk_vendedor <> 'B.F.1401'
    GROUP BY
        sv.nk_vendedor,
        ('B.F.' || sv.cd_emprvend)
),

sup_map_1401_ativo AS (
    SELECT
        sv.nk_vendedor,
        ('B.F.' || sv.cd_emprvend) AS nk_empresa,
        MIN(sv.nk_supervisor) AS nk_supervisor
    FROM stage.st_supervisorvenda sv
    WHERE sv.nk_vendedor = 'B.F.1401'
      AND sv.dt_cancelamento IS NULL
      AND sv.nk_supervisor IN (
            'B.F.434','B.F.292','B.F.80','B.F.229',
            'B.F.212','B.F.293','B.F.446','B.F.1017','B.F.253','B.F.290','B.F.214'
      )
    GROUP BY
        sv.nk_vendedor,
        ('B.F.' || sv.cd_emprvend)
),

sup_map_1401_cancelado AS (
    SELECT
        sv.nk_vendedor,
        ('B.F.' || sv.cd_emprvend) AS nk_empresa,
        MIN(sv.nk_supervisor) AS nk_supervisor
    FROM stage.st_supervisorvenda sv
    WHERE sv.nk_vendedor = 'B.F.1401'
      AND sv.dt_cancelamento IS NOT NULL
      AND sv.nk_supervisor IN (
            'B.F.434','B.F.292','B.F.80','B.F.229',
            'B.F.212','B.F.293','B.F.446','B.F.1017','B.F.253','B.F.290','B.F.214'
      )
    GROUP BY
        sv.nk_vendedor,
        ('B.F.' || sv.cd_emprvend)
),

sup_map AS (

    SELECT *
    FROM sup_map_geral

    UNION ALL

    SELECT *
    FROM sup_map_1401_ativo

    UNION ALL

    SELECT *
    FROM sup_map_1401_cancelado c
    WHERE NOT EXISTS (
        SELECT 1
        FROM sup_map_1401_ativo a
        WHERE a.nk_vendedor = c.nk_vendedor
          AND a.nk_empresa = c.nk_empresa
    )
)

SELECT

    sup.nk_supervisor,

    vendin.nk_vendedor,

    vend.nm_clifor AS nome_vendedor,

    cli.cd_clifor AS clifor,

    cli.nr_cgccpf AS cnpj,

    cli.nm_fantasia AS nome_cliente,

    vendin.dt_emissao::DATE AS dt_emissao,

    item.ds_subgrupo,

    SUM(vendin.qt_nota) AS total_ps_atendido,

    SUM(vendin.vl_tot_item_cont) AS total_vl_movtocontabil

FROM stage_comercial.st_venda_dinamica vendin

INNER JOIN sup_map sup
    ON vendin.nk_vendedor = sup.nk_vendedor
   AND vendin.nk_empresa = sup.nk_empresa

INNER JOIN stage.st_vendedor vend
    ON vendin.nk_vendedor = vend.nk_vendedor

INNER JOIN stage.st_item item
    ON vendin.nk_item = item.nk_item

LEFT JOIN stage.st_cliforendereco cfend
    ON vendin.nk_cliforendereco = cfend.nk_cliforendereco

LEFT JOIN stage.st_clifor cli
    ON cfend.nk_clifor = cli.nk_clifor

WHERE

    vendin.dt_emissao >= %s

    AND vend.ds_estado NOT LIKE '%%SAO PAULO%%'

    AND CAST(vendin.cd_movimentacao AS TEXT) NOT LIKE '69%%'

    AND (
       CASE

    WHEN vendin.nk_empresa IN (
        'B.F.1','B.F.2','B.F.4','B.F.8','B.F.9',
        'B.F.11','B.F.16','B.F.17','B.F.20',
        'B.F.22','B.F.26','B.F.29','B.F.30',
        'B.F.70','B.F.80','B.F.100'
    )
    AND vendin.cd_movimentacao IN (3067, 7585)
        THEN 'N'

    WHEN vendin.nk_empresa IN ('B.F.300','B.F.301')
    AND vendin.cd_movimentacao IN (796,7770,9890,7771)
        THEN 'N'

    WHEN vendin.nk_empresa IN (
        'B.F.350','B.F.351','B.F.352',
        'B.F.353','B.F.354'
    )
    AND vendin.cd_movimentacao = 6970
        THEN 'S'

    WHEN vendin.nk_empresa IN (
        'B.F.1','B.F.2','B.F.4','B.F.8','B.F.9',
        'B.F.11','B.F.16','B.F.17','B.F.20',
        'B.F.22','B.F.26','B.F.29','B.F.30',
        'B.F.70','B.F.80','B.F.100'
    )
    AND vendin.cd_movimentacao IN (2901,5599,6970)
        THEN 'S'

    WHEN vendin.nk_empresa = 'B.F.330'
    AND vendin.cd_movimentacao = 2884
        THEN 'S'

    WHEN vendin.nk_empresa = 'B.F.320'
    AND vendin.cd_movimentacao IN (796,5599,6550)
        THEN 'S'

    WHEN vendin.nk_empresa = 'B.F.320'
    AND vendin.cd_movimentacao IN (3162,7770,7771)
        THEN 'N'

    WHEN vendin.nk_item = 'B.F.3293422'
        THEN 'S'

    WHEN vendin.cd_movimentacao = 2332
    AND vendin.cd_contacontabil = 6722
        THEN 'N'

    WHEN vendin.cd_movimentacao IN (2235,7442)
        THEN 'N'

    WHEN vendin.cd_movimentacao = 7018
        THEN 'S'

    WHEN vendin.cd_movimentacao = 7585
    AND vendin.cd_contacontabil = 782
        THEN 'N'

    WHEN vendin.nk_empresa = 'B.F.200'
    AND vendin.cd_movimentacao IN (7770,6003,796,558,6009)
        THEN 'S'

    WHEN vendin.cd_movimentacao IN (
        69,570,7570,796,7403,559,2635,6910,
        7806,558,2340,1090,1771,1778,4855,
        7075,7745,8745,7012,7009,7006,7015,
        8003,7710,6003,6007,8075,8076,40,
        7706,7707,4040,6013,1020,3026,3027,
        6010,2883,1050,6020,4041
    )
        THEN 'S'

    WHEN vendin.cd_movimentacao IN (
        4413,6011,6012,1235,1544,4412,
        6971,7585,7661,2533,2534,
        2535,2536,2543
    )
        THEN 'N'

    WHEN vendin.nk_empresa IN (
        'B.F.1','B.F.2','B.F.3','B.F.4','B.F.5',
        'B.F.8','B.F.70','B.F.80','B.F.90',
        'B.F.100','B.F.9','B.F.11','B.F.14'
    )
    AND vendin.cd_contacontabil = 4073
        THEN 'S'

    WHEN vendin.cd_contacontabil = 4411
    AND vendin.cd_movimentacao IN (1000,1796)
        THEN 'S'

    WHEN vendin.nk_empresa = 'B.F.8'
    AND vendin.cd_contacontabil = 6104
        THEN 'S'

    WHEN vendin.cd_contacontabil = 4051
    AND vendin.cd_movimentacao IN (
        1776,1777,7002,7003,7008,
        7014,7033,8002,8008,
        8040,8042,8043
    )
        THEN 'S'

    WHEN vendin.cd_contacontabil = 4065
    AND vendin.cd_movimentacao NOT IN (407,410,1779,1771)
        THEN 'S'

    WHEN vendin.nk_empresa IN (
        'B.F.1','B.F.2','B.F.5','B.F.6',
        'B.F.7','B.F.9','B.F.11','B.F.12'
    )
    AND vendin.cd_movimentacao IN (
        69,812,7075,7706,7780
    )
        THEN 'S'

    WHEN vendin.nk_empresa = 'B.F.11'
    AND vendin.cd_movimentacao IN (896,796,1771)
        THEN 'N'

    WHEN vendin.nk_empresa IN (
        'B.F.12','B.F.4','B.F.8',
        'B.F.70','B.F.80','B.F.100'
    )
    AND vendin.cd_movimentacao IN (796,1771)
        THEN 'S'

    WHEN vendin.nk_empresa IN (
        'B.F.200','B.F.202','B.F.203'
    )
    AND vendin.cd_movimentacao IN (4040,7403)
        THEN 'S'

    WHEN vendin.nk_empresa = 'B.F.16'
    AND vendin.cd_movimentacao = 796
        THEN 'S'

    WHEN vendin.nk_empresa = 'B.F.300'
    AND vendin.cd_movimentacao IN (
        2340,6910,7402,6025,112,
        7770,5599,6911,6915
    )
        THEN 'S'

    WHEN vendin.nk_empresa IN (
        'B.F.200','B.F.201','B.F.202',
        'B.F.203','B.F.204'
    )
    AND vendin.cd_movimentacao = 6006
        THEN 'S'

    WHEN vendin.nk_empresa = 'B.F.12'
    AND vendin.cd_movimentacao = 250
        THEN 'S'

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

    vendin.dt_emissao::DATE,

    item.ds_subgrupo

ORDER BY

    vendin.dt_emissao::DATE,

    sup.nk_supervisor,

    vend.nm_clifor,

    item.ds_subgrupo;
"""

CARTEIRA_SQL = """
SELECT DISTINCT ON (clifor.cd_clifor)
    clifor.cd_clifor          AS clifor,
    clifor.nr_cgccpf          AS cnpj,
    clifor.nm_clifor          AS nome_cliente,
    vendedor.nm_clifor        AS nome_vendedor,
    sup_map.nk_supervisor     AS nk_supervisor,
    endereco.ds_municipioibge AS municipio,
    endereco.ds_estado        AS estado,
    clifor.dt_cadastramento   AS cadastro,
    clifor.dt_ultaltecadastro AS alterado
FROM stage.st_cliforendereco AS endereco
JOIN stage.st_clifor AS clifor
    ON clifor.nk_clifor = endereco.nk_clifor
JOIN stage.st_vendedor AS vendedor
    ON vendedor.nk_vendedor = endereco.nk_vendedor
JOIN stage.st_supervisorvenda AS sup_map
    ON sup_map.nk_vendedor = vendedor.nk_vendedor
WHERE endereco.st_ativo = 'S'
  AND clifor.ds_tp_clifor = 'Cliente'
  AND clifor.dt_venctocad IS NULL

  AND vendedor.nk_vendedor NOT IN (
      'B.F.60',
      'B.F.65',
      'B.F.1087',
      'B.F.1089',
      'B.F.1090',
      'B.F.1091',
      'B.F.1092',
      'B.F.1093',
      'B.F.1364'
  )

  AND vendedor.ds_estado NOT LIKE '%%SAO PAULO%%'

  AND (
        (sup_map.nk_supervisor = 'B.F.229'
         AND endereco.sg_estado = 'GO')

     OR (sup_map.nk_supervisor IN ('B.F.434','B.F.212','B.F.293','B.F.446','B.F.214','B.F.292','B.F.290')
         AND endereco.sg_estado = 'MS')

     OR (sup_map.nk_supervisor = 'B.F.212'
         AND endereco.sg_estado = 'SP')

     OR (sup_map.nk_supervisor = 'B.F.212'
         AND endereco.sg_estado = 'GO')

     OR (sup_map.nk_supervisor IN ('B.F.80','B.F.1017','B.F.253')
         AND endereco.sg_estado = 'MT')
  )

ORDER BY clifor.cd_clifor,
         clifor.dt_ultaltecadastro DESC;
"""
