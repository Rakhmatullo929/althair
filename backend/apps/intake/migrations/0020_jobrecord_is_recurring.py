from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('intake', '0019_teamlead_user'),
    ]

    operations = [
        migrations.AddField(
            model_name='jobrecord',
            name='is_recurring',
            field=models.BooleanField(
                default=False,
                verbose_name='recurring weekly',
                db_index=True,
            ),
        ),
    ]
