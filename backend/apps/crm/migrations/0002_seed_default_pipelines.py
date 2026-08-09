from django.db import migrations


DEFAULT_STAGES = (
    ("New", "green", "open"),
    ("Contacted", "blue", "open"),
    ("Qualified", "violet", "open"),
    ("Proposal", "amber", "open"),
    ("Won", "emerald", "won"),
    ("Lost", "slate", "lost"),
)


def seed_default_pipelines(apps, schema_editor):
    Organization = apps.get_model("organizations", "Organization")
    Pipeline = apps.get_model("crm", "Pipeline")
    PipelineStage = apps.get_model("crm", "PipelineStage")
    for organization in Organization.objects.iterator():
        pipeline, _ = Pipeline.objects.get_or_create(
            organization=organization,
            is_default=True,
            defaults={"name": "Sales", "is_active": True},
        )
        for position, (name, color_token, stage_type) in enumerate(DEFAULT_STAGES, start=1):
            PipelineStage.objects.get_or_create(
                organization=organization,
                pipeline=pipeline,
                position=position,
                defaults={
                    "name": name,
                    "color_token": color_token,
                    "stage_type": stage_type,
                    "is_active": True,
                },
            )


class Migration(migrations.Migration):
    dependencies = [("crm", "0001_initial")]

    operations = [migrations.RunPython(seed_default_pipelines, migrations.RunPython.noop)]
