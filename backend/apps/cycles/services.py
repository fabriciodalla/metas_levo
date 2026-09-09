from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.allocations.services import CycleCompletenessChecker

from .models import Cycle


class CycleNotCompleteError(ValidationError):
    """Ciclo não pode fechar: há alocação intermediária ainda não distribuída."""


class CycleAlreadyExistsError(ValidationError):
    """Já existe um ciclo cadastrado para esse mês/ano — não dá pra abrir de novo."""


class OpenCycleService:
    """Abertura de um novo ciclo pelo Administrador: um por mês/ano, nunca reaberto."""

    @staticmethod
    @transaction.atomic
    def open(ano: int, mes: int) -> Cycle:
        if Cycle.objects.filter(ano=ano, mes=mes).exists():
            raise CycleAlreadyExistsError(f"Já existe um ciclo cadastrado para {mes:02d}/{ano}.")

        return Cycle.objects.create(ano=ano, mes=mes)


class CloseCycleService:
    """Gate de fechamento: por padrão só fecha o ciclo se 100% da meta chegou ao nível VENDEDOR;
    `force=True` fecha mesmo incompleto — decisão do Administrador, tomada ciente do que vai
    ficar pra trás (a UI mostra a contagem de alocações presas antes de deixar confirmar)."""

    @staticmethod
    @transaction.atomic
    def close(cycle: Cycle, force: bool = False) -> Cycle:
        if cycle.status == Cycle.Status.FECHADO:
            raise CycleNotCompleteError("Ciclo já está fechado.")

        if not force:
            stuck = CycleCompletenessChecker.stuck_allocations(cycle)
            if stuck:
                total_stuck_kg = sum(item.quantity_kg for item in stuck)
                raise CycleNotCompleteError(
                    f"Ciclo incompleto: {len(stuck)} alocação(ões) presa(s) totalizando {total_stuck_kg} kg."
                )

        cycle.status = Cycle.Status.FECHADO
        cycle.closed_at = timezone.now()
        cycle.save(update_fields=["status", "closed_at"])
        return cycle
