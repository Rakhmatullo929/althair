from django.db import migrations

_DEFAULT_SHORT_SUMMARY_PROMPT = (
    "You are a dispatcher assistant for a land management and construction company.\n"
    "Read the following service request and reply with a concise 3-4 word title that "
    "captures the type of work or issue.\n\n"
    "Rules:\n"
    "- Focus on the trade or problem type (e.g. Plumbing, Framing, HVAC, Electrical, Warranty)\n"
    "- Do NOT include the requester's name or address\n"
    "- Reply with ONLY the short title, nothing else\n\n"
    "Good examples: \"Plumbing Leak\", \"Framing Request\", \"HVAC Installation\", "
    "\"Warranty Repair\", \"Punch List\"\n\n"
    "Message:\n{text}"
)

_DEFAULT_CLASSIFICATION_PROMPT = (
    "You are a work-order intake classifier for a land management / construction company.\n"
    "Classify the following message into exactly one category:\n"
    "  - immediate_resolution  (informational, no job needed)\n"
    "  - job_request_intake    (service request, repair, install, work order)\n"
    "  - review_required       (ambiguous, complex, or unclear)\n\n"
    "Reply with ONLY a JSON object: "
    "{\"category\": \"<category>\", \"confidence\": <0.0-1.0>, \"reason\": \"<brief reason>\"}\n\n"
    "Message: {text}"
)


def seed_prompts(apps, schema_editor):
    SystemPrompt = apps.get_model('intake', 'SystemPrompt')
    prompts = [
        {
            'key': 'email_short_summary',
            'text': _DEFAULT_SHORT_SUMMARY_PROMPT,
            'description': (
                'Generates a concise 3-4 word title for an email/form intake job card. '
                'Use {text} as the placeholder for the message body.'
            ),
            'is_active': True,
        },
        {
            'key': 'classification',
            'text': _DEFAULT_CLASSIFICATION_PROMPT,
            'description': (
                'Classifies incoming messages into immediate_resolution, job_request_intake, '
                'or review_required. Use {text} as the placeholder for the message.'
            ),
            'is_active': True,
        },
    ]
    for p in prompts:
        SystemPrompt.objects.get_or_create(key=p['key'], defaults={k: v for k, v in p.items() if k != 'key'})


def unseed_prompts(apps, schema_editor):
    SystemPrompt = apps.get_model('intake', 'SystemPrompt')
    SystemPrompt.objects.filter(key__in=['email_short_summary', 'classification']).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('intake', '0005_add_systemprompt'),
    ]

    operations = [
        migrations.RunPython(seed_prompts, unseed_prompts),
    ]
