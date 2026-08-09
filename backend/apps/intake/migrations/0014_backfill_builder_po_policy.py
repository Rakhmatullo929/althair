"""Retroactively apply the builder→PO rule to jobs created before it existed.

Only D.R. Horton and Fox Lane require a PO.  Existing rows for those builders
were created before JobRecord.save() enforced the rule, so they may still have
po_required=False / po_status=not_required.  Flag them so the missing-PO gap
surfaces in the dashboard and hotlist (client feedback items 21-22).

Logic is inlined (not imported from app code) so the migration stays stable.
"""

import re

from django.db import migrations

_PO_TOKEN_RE = re.compile(r'\bPO\s*:\s*\S', re.IGNORECASE)


def _requires_po(builder):
    norm = re.sub(r'\s+', ' ', re.sub(r'[.,]', '', (builder or '').strip().lower()))
    return bool(norm) and ('horton' in norm or 'fox lane' in norm or 'foxlane' in norm)


def _has_po(po_rec_note):
    return bool(_PO_TOKEN_RE.search(po_rec_note or ''))


def apply_po_policy_to_existing(apps, schema_editor):
    JobRecord = apps.get_model('intake', 'JobRecord')
    for job in JobRecord.objects.all().only('id', 'builder', 'po_required', 'po_status', 'po_rec_note'):
        if not _requires_po(job.builder):
            continue
        new_required = True
        new_status = job.po_status
        # Only recompute the auto-managed states; respect human-set pending/received.
        if job.po_status in ('not_required', 'missing'):
            new_status = 'received' if _has_po(job.po_rec_note) else 'missing'
        if job.po_required != new_required or job.po_status != new_status:
            # update() bypasses auto_now so existing ordering by updated_at is preserved.
            JobRecord.objects.filter(pk=job.pk).update(
                po_required=new_required, po_status=new_status,
            )


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('intake', '0013_job_number_seen_po_req'),
    ]

    operations = [
        migrations.RunPython(apply_po_policy_to_existing, noop),
    ]
