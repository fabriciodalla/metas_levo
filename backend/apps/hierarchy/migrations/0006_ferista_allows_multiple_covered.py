from django.db import migrations


class Migration(migrations.Migration):
    """Pedido explícito do usuário (2026-09-10, mesmo dia da revisão da Decisão 13): um ferista
    pode cobrir mais de um titular no mesmo mês. Remove a UniqueConstraint que impedia isso —
    a de covered_node (um titular só é coberto por um ferista por mês) continua."""

    dependencies = [
        ("hierarchy", "0005_feristacoverage_covering_node"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="feristacoverage",
            name="uniq_ferista_covering_month",
        ),
    ]
