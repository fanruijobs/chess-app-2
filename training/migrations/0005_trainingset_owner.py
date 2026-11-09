"""Give every training set an owner.

Adds `TrainingSet.owner` and hands every existing set to `fanrui89`, which
this migration creates if it is missing. One file, three operations: the same
migration that imposes NOT NULL is the one that supplies the data, so it is
correct both on the existing database and on a fresh one (where there are no
rows to adopt and the create is simply the first account).

Marks are untouched: they live on TrainingSetItem rows, which belong to the
set and therefore follow it to its owner with no data movement.
"""

import django.db.models.deletion
from django.conf import settings
from django.contrib.auth.hashers import make_password
from django.db import migrations, models


def create_owner_and_assign(apps, schema_editor):
    # Historical models: no set_password(), so hash explicitly. The auth
    # UserManager is preserved in migration state, so get_or_create works.
    # Deliberately NOT staff/superuser — a superuser bypasses the ownership
    # filter, which would make the feature a no-op for this very account.
    User = apps.get_model("auth", "User")
    owner, _ = User.objects.get_or_create(
        username="fanrui89",
        defaults={"password": make_password("password")},
    )
    TrainingSet = apps.get_model("training", "TrainingSet")
    # isnull filter: idempotent, and never re-stamps sets created later.
    TrainingSet.objects.filter(owner__isnull=True).update(owner=owner)


class Migration(migrations.Migration):

    dependencies = [
        ("auth", "0012_alter_user_first_name_max_length"),
        ("training", "0004_alter_trainingsetitem_puzzle_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="trainingset",
            name="owner",
            field=models.ForeignKey(
                null=True,
                blank=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="training_sets",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.RunPython(create_owner_and_assign, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="trainingset",
            name="owner",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="training_sets",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
    ]
