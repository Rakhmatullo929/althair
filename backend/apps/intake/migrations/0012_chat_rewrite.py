"""SMS chat rewrite — replace collected_data/awaiting_field with ConversationMessage."""

from uuid import uuid4

from django.db import migrations, models

import core.utils.encryption


def delete_obsolete_system_prompts(apps, schema_editor):
    SystemPrompt = apps.get_model('intake', 'SystemPrompt')
    SystemPrompt.objects.filter(key__in=[
        'sms_question_name',
        'sms_question_email',
        'sms_question_scope',
        'sms_question_service_type',
        'sms_question_site',
        'sms_question_preferred_callback',
        'kb_answer',
        'classification',
    ]).delete()


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('intake', '0011_alter_interaction_related_job'),
    ]

    operations = [
        migrations.RunSQL(
            sql='DELETE FROM intake_conversationthread;',
            reverse_sql=migrations.RunSQL.noop,
        ),
        migrations.RemoveField(
            model_name='conversationthread',
            name='collected_data',
        ),
        migrations.RemoveField(
            model_name='conversationthread',
            name='awaiting_field',
        ),
        migrations.AlterField(
            model_name='conversationthread',
            name='state',
            field=models.CharField(
                choices=[('open', 'Open'), ('closed', 'Closed')],
                db_index=True, default='open', max_length=20,
                verbose_name='state',
            ),
        ),
        migrations.AlterField(
            model_name='conversationthread',
            name='phone',
            field=models.CharField(db_index=True, max_length=30, verbose_name='phone'),
        ),
        migrations.AlterField(
            model_name='conversationthread',
            name='active_job',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=models.deletion.SET_NULL,
                related_name='conversation_threads',
                to='intake.jobrecord',
            ),
        ),
        migrations.AlterField(
            model_name='conversationthread',
            name='contact',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=models.deletion.SET_NULL,
                related_name='conversation_threads',
                to='intake.contact',
            ),
        ),
        migrations.CreateModel(
            name='ConversationMessage',
            fields=[
                ('id', models.UUIDField(default=uuid4, editable=False, primary_key=True, serialize=False)),
                ('sequence', models.PositiveIntegerField(verbose_name='sequence')),
                ('role', models.CharField(
                    choices=[('user', 'User'), ('assistant', 'Assistant'), ('tool', 'Tool')],
                    db_index=True, max_length=20, verbose_name='role',
                )),
                ('content', core.utils.encryption.EncryptedTextField(blank=True, default='', verbose_name='content')),
                ('tool_calls', models.JSONField(blank=True, default=list, verbose_name='tool calls')),
                ('tool_call_id', models.CharField(blank=True, max_length=128, verbose_name='tool call id')),
                ('tool_name', models.CharField(blank=True, max_length=64, verbose_name='tool name')),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('thread', models.ForeignKey(
                    on_delete=models.deletion.CASCADE,
                    related_name='messages',
                    to='intake.conversationthread',
                )),
            ],
            options={
                'verbose_name': 'Conversation Message',
                'verbose_name_plural': 'Conversation Messages',
                'ordering': ['thread', 'sequence'],
                'unique_together': {('thread', 'sequence')},
                'indexes': [
                    models.Index(fields=['thread', 'sequence'], name='intake_conv_msg_thr_seq'),
                ],
            },
        ),
        migrations.RunPython(delete_obsolete_system_prompts, noop_reverse),
    ]
