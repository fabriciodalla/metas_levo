from collections.abc import Iterable

from django.db import transaction
from django.db.models import QuerySet

from .models import ExternalSalespersonMapping, HierarchyClosure, HierarchyNode


class HierarchyClosureService:
    """Mantém a HierarchyClosure sincronizada com HierarchyNode.parent.

    Reconstrói a tabela inteira a cada mudança de estrutura em vez de aplicar patches
    incrementais por nó: a escala do projeto é dezenas a poucas centenas de nós (ver
    docs/architecture.md), então o custo é desprezível e evita bugs sutis de update
    incremental em reparentamento.
    """

    @staticmethod
    @transaction.atomic
    def rebuild() -> None:
        parent_by_id = dict(HierarchyNode.objects.values_list("id", "parent_id"))

        rows = []
        for node_id in parent_by_id:
            depth = 0
            current_id = node_id
            # `seen` também cobre dado corrompido (ex.: um nó apontando pra si mesmo como pai,
            # editado fora da validação — Django Admin não tinha essa checagem até este commit):
            # sem isso, subir a cadeia de pais entra em loop infinito e trava a request inteira,
            # porque este rebuild roda a cada save de QUALQUER HierarchyNode (signals.py).
            seen: set[int] = set()
            while current_id is not None and current_id not in seen:
                seen.add(current_id)
                rows.append(HierarchyClosure(ancestor_id=current_id, descendant_id=node_id, depth=depth))
                current_id = parent_by_id.get(current_id)
                depth += 1

        HierarchyClosure.objects.all().delete()
        HierarchyClosure.objects.bulk_create(rows)


class ScopeResolver:
    """Resolve a subárvore visível (inclusive) de um nó (ou vários — O5, 1:N) — base do
    isolamento de escopo por ramo."""

    @staticmethod
    def descendant_ids(node_ids: int | Iterable[int]) -> QuerySet:
        if isinstance(node_ids, int):
            node_ids = [node_ids]
        return (
            HierarchyClosure.objects.filter(ancestor_id__in=node_ids)
            .values_list("descendant_id", flat=True)
            .distinct()
        )


class ExternalSalespersonMatchingService:
    """Liga HierarchyNode (VENDEDOR) a ExternalSalespersonMapping por igualdade EXATA de nome
    contra DistributionBaseline.salesperson_name — não normaliza, não aproxima (Decisão 9, revisão
    2026-07-22: o usuário confirmou que os nomes cadastrados são idênticos aos do ERP nesta base;
    ver docs/decisions.md). Idempotente: só cria o que ainda não existe.

    Historicamente só rodava via `match_external_salespersons` (management command, disparo
    manual) — agora `sync()` também roda a cada abertura da tela de distribuição (`allocations/
    views.py`, `distribution_context`/`subgroup_distribution_context`), sem custo de rede (só
    tabelas locais já sincronizadas). Bug real, 2026-08-07: Rafael Pereira de Quadros virou
    Vendedor sob ele mesmo com histórico real no banco, mas a sugestão saiu zerada porque
    ninguém lembrou de rodar o comando manual — o vínculo só apareceu depois de rodá-lo à mão.
    """

    @staticmethod
    @transaction.atomic
    def sync() -> tuple[list[str], list[str]]:
        """Retorna (nomes recém-mapeados, nomes de Vendedor sem correspondência exata — esses
        seguem exigindo curadoria manual via Django Admin, igual sempre foi)."""
        # Import tardio: hierarchy não depende de sales_history em nenhum outro lugar além
        # deste service — evita virar uma dependência de módulo fixa entre os dois apps.
        from apps.sales_history.models import DistributionBaseline

        already_mapped_node_ids = set(
            ExternalSalespersonMapping.objects.values_list("hierarchy_node_id", flat=True)
        )
        already_mapped_names = set(ExternalSalespersonMapping.objects.values_list("external_name", flat=True))

        candidate_nodes = HierarchyNode.objects.filter(level=HierarchyNode.Level.VENDEDOR).exclude(
            id__in=already_mapped_node_ids
        )
        baseline_names = set(
            name
            for name in DistributionBaseline.objects.values_list("salesperson_name", flat=True).distinct()
            if name
        )
        available_names = baseline_names - already_mapped_names

        created = []
        unmatched = []
        for node in candidate_nodes:
            if node.nome in available_names:
                ExternalSalespersonMapping.objects.create(external_name=node.nome, hierarchy_node=node)
                created.append(node.nome)
            else:
                unmatched.append(node.nome)

        return created, unmatched
