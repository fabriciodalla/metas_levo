from django.db import models


class AccumulatedSale(models.Model):
    """Espelho local do resultado da query de acumulado (Postgres externo, somente leitura).

    Uma linha por combinação de supervisor/vendedor/cliente/subgrupo/data — o mesmo agrupamento
    da query original (8 colunas, não só vendedor+cliente+subgrupo+data: o mesmo vendedor pode
    aparecer com nk_supervisor diferente conforme a empresa da venda). Sem constraint de
    unicidade de negócio por isso; a idempotência do sync vem de apagar a tabela inteira antes de
    reinserir (`SalesHistorySyncService.sync_accumulated`), não de upsert por chave — e essa
    troca (apagar tudo + inserir) só é segura porque roda dentro do advisory lock
    `apps.sales_history.services.sync_lock`, que serializa CLI e botão do SPA entre si (sem o
    lock, dois syncs concorrentes duplicavam tudo — incidente de 2026-09-02). Repovoada pelo
    SalesHistorySyncService a cada sincronização; nada aqui é escrito pela aplicação.
    """

    nk_supervisor = models.CharField(max_length=50)
    nk_vendedor = models.CharField(max_length=50)
    salesperson_name = models.CharField(max_length=150)

    client_code = models.BigIntegerField()
    cnpj = models.CharField(max_length=20, blank=True)
    client_name = models.CharField(max_length=150, blank=True)

    sale_date = models.DateField()
    subgroup_name = models.CharField(max_length=60)

    total_quantity = models.DecimalField(max_digits=18, decimal_places=6)
    total_value = models.DecimalField(max_digits=18, decimal_places=3)

    synced_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["sale_date"]),
            models.Index(fields=["nk_vendedor"]),
        ]

    def __str__(self):
        return f"{self.nk_vendedor} — {self.subgroup_name} ({self.sale_date})"


class ClientPortfolioSnapshot(models.Model):
    """Espelho local da carteira (Postgres externo, somente leitura) — foto do momento atual.

    Sem dimensão de data: cada sincronização substitui a tabela inteira, não acumula.
    """

    client_code = models.BigIntegerField(unique=True)
    cnpj = models.CharField(max_length=20, blank=True)
    client_name = models.CharField(max_length=150)
    salesperson_name = models.CharField(max_length=150)
    nk_supervisor = models.CharField(max_length=50)

    municipio = models.CharField(max_length=60, blank=True)
    estado = models.CharField(max_length=60, blank=True)

    registered_at = models.DateField(null=True, blank=True)
    last_changed_at = models.DateField(null=True, blank=True)

    synced_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.client_name} — {self.salesperson_name}"


class DistributionBaseline(models.Model):
    """Base para os cálculos de distribuição de metas (P1-P4).

    Reatribui o acumulado de vendas ao vendedor ATUAL da carteira do cliente (ligação pelo
    `client_code`/clifor, comum às duas tabelas) — não importa quem historicamente vendeu, importa
    quanto aquele cliente comprou, e isso conta para quem hoje é responsável por ele (mesmo que
    esse vendedor nunca tenha vendido pessoalmente pra esse cliente). Agrupado por
    ano/mês/vendedor/subgrupo, com a quantidade somada.

    Reconstruída inteira por `DistributionBaselineService.rebuild()` a partir de `AccumulatedSale`
    + `ClientPortfolioSnapshot` já sincronizados — não consulta o Postgres externo diretamente.
    Clientes do acumulado sem entrada correspondente na carteira atual entram com
    `salesperson_name=None` (em vez de ficar de fora): não há vendedor vigente para atribuir esse
    histórico a nível de Vendedor/Supervisor (P2-P4), mas o volume continua real e deve contar na
    sugestão de meta do Gerente (P1, `SalesHistoryProvider.group_history`, que soma por subgrupo
    sem filtrar por vendedor).

    `total_quantity` é sempre inteiro (KG, sem casas decimais — mesma convenção do resto do
    produto): a soma do agrupamento é arredondada (>= 0,5 sobe, < 0,5 desce) antes de persistir.
    """

    ano = models.PositiveIntegerField()
    mes = models.PositiveSmallIntegerField()
    salesperson_name = models.CharField(max_length=150, null=True, blank=True)
    subgroup_name = models.CharField(max_length=60)
    total_quantity = models.DecimalField(max_digits=18, decimal_places=0)

    computed_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["ano", "mes", "salesperson_name", "subgroup_name"],
                name="uniq_distribution_baseline_row",
            ),
        ]
        indexes = [
            models.Index(fields=["ano", "mes"]),
            models.Index(fields=["salesperson_name"]),
        ]

    def __str__(self):
        return f"{self.mes:02d}/{self.ano} — {self.salesperson_name} — {self.subgroup_name}"
