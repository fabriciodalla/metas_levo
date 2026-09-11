import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    """Revisão da Decisão 13 (2026-09-10): ferista passa a ser um Vendedor normal da hierarquia
    (`covering_node`), não mais um nome livre (`external_name`) — ver `apps/hierarchy/models.py`.
    Sem migração de dados: `FeristaCoverage` estava vazia (0 linhas) neste ponto."""

    dependencies = [
        ("hierarchy", "0004_remove_regional_level"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="feristacoverage",
            name="uniq_ferista_coverage_month",
        ),
        migrations.RemoveField(
            model_name="feristacoverage",
            name="external_name",
        ),
        migrations.AddField(
            model_name="feristacoverage",
            name="covering_node",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="covering_ferista_coverages",
                to="hierarchy.hierarchynode",
                default=None,
            ),
            preserve_default=False,
        ),
        migrations.AddConstraint(
            model_name="feristacoverage",
            constraint=models.UniqueConstraint(
                fields=("covering_node", "ano", "mes"), name="uniq_ferista_covering_month"
            ),
        ),
        migrations.AddConstraint(
            model_name="feristacoverage",
            constraint=models.UniqueConstraint(
                fields=("covered_node", "ano", "mes"), name="uniq_ferista_covered_month"
            ),
        ),
    ]
