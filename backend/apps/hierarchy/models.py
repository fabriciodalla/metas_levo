from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Case, IntegerField, Value, When


class HierarchyNodeQuerySet(models.QuerySet):
    def visible_to(self, user):
        if user.is_admin:
            return self

        node_ids = list(user.hierarchy_nodes.values_list("id", flat=True))
        if not node_ids:
            return self.none()

        from .services import ScopeResolver

        return self.filter(id__in=ScopeResolver.descendant_ids(node_ids))

    def by_seniority(self):
        """Ordena da posição mais sênior (Gerente) pra menos sênior (Vendedor), com `id` como
        desempate — define qual posição é a "principal" de alguém que acumula mais de uma
        (Decisão 10/O5, ver `UserAccountSerializer._sync_position`). Bug real, 2026-08-07:
        ordenar por `id` (ordem de criação) em vez de senioridade fazia uma promoção — cargo
        mais alto registrado depois, como posição extra — nunca virar "a principal"; editar o
        cargo principal continuava mexendo no cargo antigo e mais baixo, e como a posição extra
        já tinha o nível/superior que se queria dar ao principal, isso criava um cargo duplicado
        em vez de reconhecer que a pessoa já tinha o cargo novo."""
        level_order = [choice[0] for choice in HierarchyNode.Level.choices]
        rank = Case(
            *(When(level=level, then=Value(i)) for i, level in enumerate(level_order)),
            output_field=IntegerField(),
        )
        return self.annotate(_seniority_rank=rank).order_by("_seniority_rank", "id")


class HierarchyNode(models.Model):
    class Level(models.TextChoices):
        GERENTE = "GERENTE", "Gerente"
        LOCAL = "LOCAL", "Coordenador Local"
        SUPERVISOR = "SUPERVISOR", "Supervisor"
        VENDEDOR = "VENDEDOR", "Vendedor"

    objects = HierarchyNodeQuerySet.as_manager()

    level = models.CharField(max_length=20, choices=Level.choices)
    parent = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.PROTECT, related_name="children"
    )
    nome = models.CharField(max_length=255)
    ativo = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [models.Index(fields=["level", "ativo"])]

    def __str__(self):
        return f"{self.get_level_display()}: {self.nome}"

    def clean(self):
        # Só o Django Admin edita hierarquia de fato (Decisão 4) e, ao contrário da API
        # (`HierarchyNodeSerializer.validate`), o ModelForm padrão do Admin não teria nenhuma
        # checagem de nível/pai sem isso — abriria brecha pra um nó virar seu próprio pai (ou
        # o pai de um nível incompatível) só por um clique errado no autocomplete.
        level_order = [choice[0] for choice in self.Level.choices]
        level_index = level_order.index(self.level)
        if level_index == 0:
            if self.parent_id is not None:
                raise ValidationError({"parent": "Gerente não pode ter nó pai."})
        else:
            if self.parent_id is None:
                raise ValidationError({"parent": "Esse nível exige um nó pai."})
            if self.parent_id == self.pk:
                raise ValidationError({"parent": "Um nó não pode ser seu próprio superior."})
            expected_parent_level = level_order[level_index - 1]
            if self.parent.level != expected_parent_level:
                raise ValidationError(
                    {
                        "parent": (
                            f"Pai precisa ser do nível {expected_parent_level}, "
                            f"mas o nó escolhido é {self.parent.level}."
                        )
                    }
                )


class HierarchyClosure(models.Model):
    """Closure table: uma linha por par (ancestral, descendente), incluindo profundidade 0 (o próprio nó)."""

    ancestor = models.ForeignKey(HierarchyNode, on_delete=models.CASCADE, related_name="closure_descendants")
    descendant = models.ForeignKey(HierarchyNode, on_delete=models.CASCADE, related_name="closure_ancestors")
    depth = models.PositiveIntegerField()

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["ancestor", "descendant"], name="uniq_hierarchy_closure_pair"),
        ]
        indexes = [
            models.Index(fields=["ancestor"]),
            models.Index(fields=["descendant"]),
        ]


class ExternalSalespersonMapping(models.Model):
    """Mapeia o nome do vendedor exposto pela carteira do Postgres externo (`salesperson_name` em
    `DistributionBaseline`/`ClientPortfolioSnapshot` — texto livre, sem código estável) para o
    `HierarchyNode` interno correspondente. Parte de O3/O5 (ver docs/open-questions.md).

    Populado automaticamente por igualdade EXATA de nome contra `DistributionBaseline.
    salesperson_name` (Decisão 9, revisão 2026-07-22) — ver `ExternalSalespersonMatchingService`
    em `hierarchy/services.py`, que roda sozinho a cada abertura da tela de distribuição. Nome que
    não bate exato (formatação diferente, homônimo, mudou ao longo do tempo) não é aproximado
    automaticamente — continua exigindo curadoria manual via Django Admin.
    """

    external_name = models.CharField(max_length=150, unique=True)
    hierarchy_node = models.ForeignKey(
        HierarchyNode, on_delete=models.PROTECT, related_name="external_salesperson_mappings"
    )

    def __str__(self):
        return f"{self.external_name} -> {self.hierarchy_node}"


class FeristaCoverage(models.Model):
    """Cobertura de férias: um vendedor "ferista" (identificado só pelo nome livre do ERP, igual
    `ExternalSalespersonMapping.external_name` — não tem nó próprio na hierarquia, nem precisa)
    vende em nome de um Vendedor titular já cadastrado enquanto ele está de férias. Decisão do
    usuário, 2026-07-22 (ver Decisão 13 em docs/decisions.md): a granularidade é **mês/ano**, não
    data exata — `DistributionBaseline` (fonte do histórico) só existe por mês, então precisão de
    dia seria falsa (não dá pra fatiar um mês de venda entre dois titulares).

    `SalesHistoryProvider.target_history` usa isso pra redirecionar, mês a mês, o volume vendido
    pelo ferista pro nó do titular coberto — nos meses sem cobertura registrada, o nome do ferista
    simplesmente não conta pra ninguém (mesmo comportamento de um nome sem
    `ExternalSalespersonMapping`).
    """

    external_name = models.CharField(max_length=150)
    covered_node = models.ForeignKey(
        HierarchyNode, on_delete=models.PROTECT, related_name="ferista_coverages"
    )
    ano = models.PositiveSmallIntegerField()
    mes = models.PositiveSmallIntegerField()

    class Meta:
        constraints = [
            # Um ferista só cobre uma pessoa por mês — a granularidade mensal não permite fatiar.
            models.UniqueConstraint(
                fields=["external_name", "ano", "mes"], name="uniq_ferista_coverage_month"
            ),
            models.CheckConstraint(
                condition=models.Q(mes__gte=1, mes__lte=12), name="ferista_coverage_mes_valido"
            ),
        ]

    def clean(self):
        if self.covered_node_id and self.covered_node.level != HierarchyNode.Level.VENDEDOR:
            raise ValidationError({"covered_node": "O nó coberto precisa ser um Vendedor."})

    def __str__(self):
        return f"{self.external_name} cobriu {self.covered_node} em {self.mes:02d}/{self.ano}"
