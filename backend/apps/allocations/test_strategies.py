from django.test import SimpleTestCase

from .strategies import (
    DistributionStrategyRegistry,
    LargestRemainderRoundingPolicy,
    ManualDistributionStrategy,
    MonthlyQuantity,
    RecentAverageDistributionStrategy,
    SeasonalTrendDistributionStrategy,
    SeasonalTrendSuggestionStrategy,
    StrategyNotConfiguredError,
    default_distribution_registry,
)


def _monthly_history(start_ano: int, start_mes: int, values: list[float]) -> list[MonthlyQuantity]:
    """Monta um histórico cronológico consecutivo a partir de (start_ano, start_mes)."""
    history = []
    ano, mes = start_ano, start_mes
    for value in values:
        history.append(MonthlyQuantity(ano=ano, mes=mes, quantity_kg=value))
        mes += 1
        if mes > 12:
            mes = 1
            ano += 1
    return history


class ManualDistributionStrategyTests(SimpleTestCase):
    def test_distributes_exactly_the_given_quantities(self):
        strategy = ManualDistributionStrategy({1: 60, 2: 40})

        result = strategy.distribute(total_kg=100, target_ids=[1, 2])

        self.assertEqual(result, {1: 60, 2: 40})

    def test_raises_when_missing_quantity_for_a_target(self):
        strategy = ManualDistributionStrategy({1: 60})

        with self.assertRaises(ValueError):
            strategy.distribute(total_kg=100, target_ids=[1, 2])


class LargestRemainderRoundingPolicyTests(SimpleTestCase):
    def setUp(self):
        self.policy = LargestRemainderRoundingPolicy()

    def test_sum_always_closes_exactly_with_non_divisible_total(self):
        result = self.policy.round_to_close(100, {1: 1, 2: 1, 3: 1})

        self.assertEqual(sum(result.values()), 100)
        self.assertEqual(result, {1: 34, 2: 33, 3: 33})

    def test_largest_fractional_remainder_gets_the_extra_kg(self):
        result = self.policy.round_to_close(10, {1: 2, 2: 1})

        self.assertEqual(result, {1: 7, 2: 3})

    def test_raises_on_empty_proportions(self):
        with self.assertRaises(ValueError):
            self.policy.round_to_close(100, {})

    def test_raises_when_proportions_sum_to_zero(self):
        with self.assertRaises(ValueError):
            self.policy.round_to_close(100, {1: 0, 2: 0})


class DistributionStrategyRegistryTests(SimpleTestCase):
    def test_default_registry_resolves_manual_mode_for_distributing_levels(self):
        for level in ("GERENTE", "LOCAL", "SUPERVISOR"):
            strategy = default_distribution_registry.resolve(level, "MANUAL", quantities_by_target={1: 100})
            self.assertEqual(strategy.distribute(100, [1]), {1: 100})

    def test_default_registry_resolves_auto_mode_for_gerente_and_p2_p3_p4_levels(self):
        # Extensão pedida pelo usuário em 2026-07-22 (ver Decisão 6): Gerente->Local (P2, com o
        # nível Regional removido — Decisão 14) usa a mesma fórmula tendência+sazonalidade já
        # aprovada para P3-P4, como sugestão editável — não é uma fórmula nova.
        history = {
            1: _monthly_history(2024, 1, [100] * 12),
            2: _monthly_history(2024, 1, [200] * 12),
        }
        for level in ("GERENTE", "LOCAL", "SUPERVISOR"):
            strategy = default_distribution_registry.resolve(level, "AUTO", history_by_target=history)
            result = strategy.distribute(100, [1, 2])
            self.assertEqual(sum(result.values()), 100)

    def test_resolve_raises_for_unregistered_level(self):
        registry = DistributionStrategyRegistry()

        with self.assertRaises(StrategyNotConfiguredError):
            registry.resolve("VENDEDOR", "MANUAL")


class SeasonalTrendSuggestionStrategyTests(SimpleTestCase):
    def test_pure_linear_trend_with_no_seasonal_variation_projects_exactly(self):
        # Série perfeitamente linear (sem ruído): a reta ajusta com resíduo zero, então o índice
        # sazonal fica neutro (1.0) em todos os meses — a projeção é só a reta extrapolada.
        values = [100 + 10 * i for i in range(1, 13)]  # 110, 120, ..., 220
        strategy = SeasonalTrendSuggestionStrategy(history_by_group={1: _monthly_history(2024, 1, values)})

        result = strategy.suggest(group_ids=[1], period_months=12)

        self.assertEqual(result, {1: 230})  # 100 + 10*13

    def test_seasonal_spike_pulls_forecast_above_flat_baseline(self):
        flat_strategy = SeasonalTrendSuggestionStrategy(
            history_by_group={1: _monthly_history(2024, 1, [100] * 12)}
        )
        spiked_strategy = SeasonalTrendSuggestionStrategy(
            history_by_group={1: _monthly_history(2024, 1, [100] * 11 + [200])}
        )

        flat_result = flat_strategy.suggest(group_ids=[1], period_months=12)
        spiked_result = spiked_strategy.suggest(group_ids=[1], period_months=12)

        self.assertEqual(flat_result, {1: 100})
        self.assertGreater(spiked_result[1], flat_result[1])

    def test_uses_only_the_trailing_window_requested(self):
        # 12 meses de ruído alto seguidos por 3 meses lineares limpos — pedindo só os últimos 3,
        # o resultado deve ignorar o ruído anterior.
        noisy_then_clean = [999, 1, 999, 1, 999, 1, 999, 1, 999, 1, 999, 1] + [100, 110, 120]
        strategy = SeasonalTrendSuggestionStrategy(
            history_by_group={1: _monthly_history(2023, 1, noisy_then_clean)}
        )

        result = strategy.suggest(group_ids=[1], period_months=3)

        self.assertEqual(result, {1: 130})  # 100, 110, 120 -> reta perfeita, projeta 130

    def test_raises_when_group_has_no_history(self):
        strategy = SeasonalTrendSuggestionStrategy(history_by_group={})

        with self.assertRaises(ValueError):
            strategy.suggest(group_ids=[1], period_months=12)

    def test_raises_with_fewer_than_two_months(self):
        strategy = SeasonalTrendSuggestionStrategy(history_by_group={1: _monthly_history(2024, 1, [100])})

        with self.assertRaises(ValueError):
            strategy.suggest(group_ids=[1], period_months=12)


class SeasonalTrendDistributionStrategyTests(SimpleTestCase):
    def test_distributes_proportionally_to_projection_and_closes_exactly(self):
        # Alvo 2 tem histórico maior -> deve receber mais que o alvo 1, e a soma fecha exata.
        strategy = SeasonalTrendDistributionStrategy(
            history_by_target={
                1: _monthly_history(2024, 1, [100] * 12),
                2: _monthly_history(2024, 1, [300] * 12),
            },
            rounding_policy=LargestRemainderRoundingPolicy(),
        )

        result = strategy.distribute(total_kg=100, target_ids=[1, 2])

        self.assertEqual(sum(result.values()), 100)
        self.assertGreater(result[2], result[1])

    def test_target_without_history_gets_zero_weight(self):
        strategy = SeasonalTrendDistributionStrategy(
            history_by_target={1: _monthly_history(2024, 1, [100] * 12)},
            rounding_policy=LargestRemainderRoundingPolicy(),
        )

        result = strategy.distribute(total_kg=100, target_ids=[1, 2])

        self.assertEqual(result, {1: 100, 2: 0})


class RecentAverageDistributionStrategyTests(SimpleTestCase):
    """Default do modo `AUTO` em todos os níveis desde 2026-09-03 (ver Decisão 6) — proporção
    pela média dos últimos 3 meses, com garantia de nunca deixar a sugestão vazia."""

    def test_distributes_proportionally_to_last_3_months_average_and_closes_exactly(self):
        strategy = RecentAverageDistributionStrategy(
            history_by_target={
                1: _monthly_history(2024, 1, [100] * 12),
                2: _monthly_history(2024, 1, [300] * 12),
            },
            rounding_policy=LargestRemainderRoundingPolicy(),
        )

        result = strategy.distribute(total_kg=100, target_ids=[1, 2])

        self.assertEqual(result, {1: 25, 2: 75})

    def test_splits_equally_when_no_target_has_any_history(self):
        # Garantia confirmada com o usuário (2026-09-03): mesmo sem NENHUM histórico pra nenhum
        # alvo, a sugestão nunca fica vazia — reparte em partes iguais em vez de levantar erro.
        strategy = RecentAverageDistributionStrategy(
            history_by_target={},
            rounding_policy=LargestRemainderRoundingPolicy(),
        )

        result = strategy.distribute(total_kg=100, target_ids=[1, 2, 3])

        self.assertEqual(sum(result.values()), 100)
        self.assertEqual(result, {1: 34, 2: 33, 3: 33})

    def test_zeroes_targets_below_min_share_and_redistributes_to_the_rest(self):
        strategy = RecentAverageDistributionStrategy(
            history_by_target={
                1: _monthly_history(2024, 1, [900] * 12),
                2: _monthly_history(2024, 1, [1] * 12),  # ~0.11% do total -> abaixo do piso de 0.5%
            },
            rounding_policy=LargestRemainderRoundingPolicy(),
        )

        result = strategy.distribute(total_kg=100, target_ids=[1, 2])

        self.assertEqual(result, {1: 100, 2: 0})
