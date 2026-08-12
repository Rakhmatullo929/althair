from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("sms", "0001_initial")]

    operations = [
        migrations.AddField(
            model_name="smsoutboundattempt",
            name="next_retry_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="smsoutboundattempt",
            name="retryable",
            field=models.BooleanField(default=False),
        ),
    ]
