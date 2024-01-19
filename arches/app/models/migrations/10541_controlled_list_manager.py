# Generated by Django 4.2.9 on 2024-01-19 12:11

import uuid

import django.db.models.deletion
from django.db import migrations, models

from arches.app.models.fields.i18n import I18n_String

from arches.app.models.fields.i18n import I18n_String

def add_plugins(apps, schema_editor):
    Plugin = apps.get_model("models", "Plugin")

    Plugin(
        pluginid="60aa3e80-4aea-4042-a76e-5a872b1c36a0",
        name=I18n_String("Controlled List Manager"),
        icon="fa fa-list",
        component="views/components/plugins/controlled-list-manager",
        componentname="controlled-list-manager",
        config={"show": True},
        slug="controlled-list-manager",
        sortorder=0,
    ).save()


def remove_plugin(apps, schema_editor):
    Plugin = apps.get_model("models", "Plugin")

    Plugin.objects.filter(slug="controlled-list-manager").delete()


class Migration(migrations.Migration):
    dependencies = [
        ("models", "9525_add_published_graph_edits"),
    ]

    operations = [
        migrations.RunPython(add_plugins, remove_plugin),
        migrations.CreateModel(
            name="ControlledList",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("name", models.CharField(max_length=127)),
                ("dynamic", models.BooleanField(default=False)),
            ],
            options={
                "db_table": "controlled_lists",
            },
        ),
        migrations.CreateModel(
            name="ControlledListItem",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("uri", models.URLField(max_length=2048, null=True, unique=True)),
                (
                    "list",
                    models.ForeignKey(
                        db_column="listid", on_delete=django.db.models.deletion.CASCADE, related_name="items", to="models.controlledlist"
                    ),
                ),
                (
                    "parent",
                    models.ForeignKey(
                        null=True, on_delete=django.db.models.deletion.CASCADE, related_name="children", to="models.controlledlistitem"
                    ),
                ),
            ],
            options={
                "db_table": "controlled_list_items",
            },
        ),
        migrations.CreateModel(
            name="Label",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("value", models.CharField(max_length=1024)),
                (
                    "item",
                    models.ForeignKey(
                        db_column="itemid",
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="labels",
                        to="models.controlledlistitem",
                    ),
                ),
                (
                    "language",
                    models.ForeignKey(
                        db_column="languageid", on_delete=django.db.models.deletion.PROTECT, to="models.language", to_field="code"
                    ),
                ),
                (
                    "value_type",
                    models.ForeignKey(
                        limit_choices_to={"category": "label"}, on_delete=django.db.models.deletion.PROTECT, to="models.dvaluetype"
                    ),
                ),
            ],
            options={
                "db_table": "controlled_list_labels",
            },
        ),
        migrations.AddConstraint(
            model_name="label",
            constraint=models.UniqueConstraint(
                fields=("item", "value", "value_type", "language"), name="unique_item_value_valuetype_language"
            ),
        ),
    ]
