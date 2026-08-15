import uuid

from django.db import migrations, models
import django.db.models.deletion
import organizations.models


MANUAL_FEATURE_VALUES = {
    "crm": True,
    "crm_read": True,
    "billing_access": True,
    "data_export": True,
    "web_chat": True,
    "instagram": True,
    "telegram": True,
    "gmail": True,
    "sms": True,
    "voice": True,
    "ai_runtime": True,
    "ai_autopilot": True,
    "api_access": True,
    "custom_tools": True,
    "max_members": 25,
    "max_branches": 25,
    "max_channel_connections": 25,
    "max_web_chat_installations": 25,
    "max_instagram_connections": 25,
    "max_telegram_bots": 25,
    "max_gmail_connections": 25,
    "max_sms_connections": 25,
    "max_voice_connections": 25,
    "monthly_ai_input_tokens": 1000000,
    "monthly_ai_output_tokens": 250000,
    "monthly_ai_runs": 10000,
    "monthly_sms_segments": 10000,
    "monthly_voice_minutes": "1000",
    "monthly_external_messages": 50000,
    "retention_days": 90,
}


def migrate_plan_catalog(apps, schema_editor):
    LegacyPlan = apps.get_model("control_plane", "LegacyPlanCatalog")
    Plan = apps.get_model("control_plane", "PlanCatalog")
    Entitlement = apps.get_model("control_plane", "OrganizationEntitlement")
    mapping = {}
    for legacy in LegacyPlan.objects.all().iterator():
        feature_values = dict(MANUAL_FEATURE_VALUES if legacy.key == "manual" else {})
        feature_values.update(dict(legacy.feature_flags or {}))
        feature_values.update(dict(legacy.default_limits or {}))
        plan = Plan.objects.create(
            key=legacy.key,
            version=1,
            display_name=legacy.display_name,
            description="Migrated from the original control-plane plan catalog.",
            status="active" if legacy.active else "retired",
            audience="internal" if legacy.key == "manual" else "self_serve",
            feature_values=feature_values,
            internal_notes=legacy.internal_notes,
            published_at=legacy.created_at if legacy.active else None,
            retired_at=legacy.updated_at if not legacy.active else None,
        )
        mapping[legacy.pk] = plan.pk
    for entitlement in Entitlement.objects.all().iterator():
        Entitlement.objects.filter(pk=entitlement.pk).update(plan_version_id=mapping[entitlement.plan_id])


class Migration(migrations.Migration):
    dependencies = [("control_plane", "0001_initial")]

    operations = [
        migrations.RenameModel(old_name="PlanCatalog", new_name="LegacyPlanCatalog"),
        migrations.CreateModel(
            name="PlanCatalog",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("key", models.SlugField(max_length=80)),
                ("version", models.PositiveIntegerField(default=1)),
                ("display_name", models.CharField(max_length=160)),
                ("description", models.TextField(blank=True, max_length=2000)),
                ("status", models.CharField(choices=[("draft", "Draft"), ("active", "Active"), ("retired", "Retired")], db_index=True, default="draft", max_length=16)),
                ("audience", models.CharField(choices=[("self_serve", "Self serve"), ("sales_assisted", "Sales assisted"), ("internal", "Internal")], db_index=True, default="self_serve", max_length=24)),
                ("feature_values", models.JSONField(default=dict, validators=[organizations.models.validate_json_object])),
                ("internal_notes", models.TextField(blank=True, max_length=2000)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("published_at", models.DateTimeField(blank=True, null=True)),
                ("retired_at", models.DateTimeField(blank=True, null=True)),
            ],
            options={"ordering": ["key", "-version"]},
        ),
        migrations.AddConstraint(
            model_name="plancatalog",
            constraint=models.UniqueConstraint(fields=("key", "version"), name="unique_plan_catalog_version"),
        ),
        migrations.AddField(
            model_name="organizationentitlement",
            name="plan_version",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="organization_entitlements",
                to="control_plane.plancatalog",
            ),
        ),
        migrations.AddField(
            model_name="organizationentitlement",
            name="override_expires_at",
            field=models.DateTimeField(blank=True, db_index=True, null=True),
        ),
        migrations.AddField(
            model_name="organizationentitlement",
            name="override_reason",
            field=models.CharField(blank=True, max_length=1000),
        ),
        migrations.RunPython(migrate_plan_catalog, migrations.RunPython.noop),
        migrations.RemoveField(model_name="organizationentitlement", name="plan"),
        migrations.RenameField(model_name="organizationentitlement", old_name="plan_version", new_name="plan"),
        migrations.AlterField(
            model_name="organizationentitlement",
            name="plan",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="organization_entitlements",
                to="control_plane.plancatalog",
            ),
        ),
        migrations.DeleteModel(name="LegacyPlanCatalog"),
    ]
