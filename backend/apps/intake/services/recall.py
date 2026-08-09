"""
Context-lookup recall service.

Read-only helpers that surface a caller's already-known location context
(site / address / city / state / community / builder / lot) from existing
``Contact`` + ``JobRecord`` rows, so the external Voice platform can CONFIRM
details instead of re-asking them.

No new tables, no migrations, no business-logic changes — pure read queries
over data the intake pipeline already stores.
"""

import logging
import re

from intake.models import Contact, JobRecord

logger = logging.getLogger(__name__)

# How many recent jobs to scan / distinct locations to return per lookup.
RECENT_JOBS_LIMIT = 10
KNOWN_LOCATIONS_LIMIT = 10

_STATE_RE = re.compile(r'^[A-Za-z]{2}$')


def _normalize_digits(value: str) -> str:
    return re.sub(r'\D', '', value or '')


def _split_address(site: str):
    """Best-effort split of a stored ``site`` string into (address, city, state).

    ``site`` usually holds the full address, e.g.
    ``"189 East Avenue, Pittsburgh, PA 15201"``.  The first segment is treated as
    a street address only when it starts with a number; city/state are recovered
    from the trailing comma segments.  Returns ('', '', '') when nothing usable.
    """
    site = (site or '').strip()
    if not site:
        return '', '', ''
    parts = [p.strip() for p in site.split(',') if p.strip()]
    if len(parts) == 1:
        only = parts[0]
        return (only if only[:1].isdigit() else ''), '', ''

    address = parts[0] if parts[0][:1].isdigit() else ''
    city = ''
    state = ''
    last_tokens = parts[-1].split()
    if last_tokens and _STATE_RE.match(last_tokens[0]):
        state = last_tokens[0].upper()
        city = parts[-2] if len(parts) >= 2 else ''
    else:
        # No clear trailing state code — the segment right after the street is
        # the city (or the first segment when there is no street address).
        city = parts[1] if address else parts[0]
    return address, city, state


def _city_state_from_contact(contact) -> tuple:
    """Pull (city, state) from ``Contact.metadata['city_state']`` when present."""
    if contact is None:
        return '', ''
    meta = getattr(contact, 'metadata', None) or {}
    cs = (meta.get('city_state') or '').strip()
    if not cs:
        return '', ''
    parts = [p.strip() for p in cs.split(',') if p.strip()]
    if not parts:
        return '', ''
    city = parts[0]
    state = ''
    if len(parts) >= 2:
        tokens = parts[1].split()
        if tokens and _STATE_RE.match(tokens[0]):
            state = tokens[0].upper()
    return city, state


def _location_from_job(job, contact=None) -> dict:
    """Build the unified location dict from a JobRecord (+ contact fallback)."""
    address, city, state = _split_address(job.site)
    if (not city or not state) and contact is not None:
        c2, s2 = _city_state_from_contact(contact)
        city = city or c2
        state = state or s2
    return {
        'site': job.site or '',
        'address': address,
        'city': city,
        'state': state,
        # township is not stored anywhere today (per architecture audit) — always ''.
        'township': '',
        'community': job.community or '',
        'builder': job.builder or '',
        'lot_phase': job.project or '',
    }


def _dedupe_locations(locations):
    """Distinct list keyed by (site, community), preserving recency order."""
    seen = set()
    out = []
    for loc in locations:
        key = (loc['site'].lower(), loc['community'].lower())
        if key in seen:
            continue
        seen.add(key)
        out.append(loc)
        if len(out) >= KNOWN_LOCATIONS_LIMIT:
            break
    return out


def _contact_block(contact) -> dict:
    if contact is None:
        return None
    return {'name': contact.name or '', 'phone': contact.phone or ''}


def _result(match_type, found, contact, locations) -> dict:
    return {
        'found': found,
        'match_type': match_type,
        'contact': _contact_block(contact),
        'most_recent': locations[0] if locations else None,
        'known_locations': locations,
    }


def lookup_context(*, organization, phone: str = '', site: str = '', community: str = '', address: str = '') -> dict:
    """Resolve known location context. Precedence: phone > site > community > address."""
    phone = (phone or '').strip()
    site = (site or '').strip()
    community = (community or '').strip()
    address = (address or '').strip()

    if phone:
        return _lookup_by_phone(organization, phone)
    if site:
        return _lookup_by_job_field(organization, 'site', site, 'site')
    if community:
        return _lookup_by_job_field(organization, 'community', community, 'community')
    if address:
        # Address is stored inside JobRecord.site — search there.
        return _lookup_by_job_field(organization, 'site', address, 'address')
    return _result('none', False, None, [])


def _lookup_by_phone(organization, phone: str) -> dict:
    digits = _normalize_digits(phone)
    core = digits[-10:] if len(digits) >= 10 else digits
    contact = None
    if core:
        contact = (
            Contact.objects.filter(organization=organization, phone__icontains=core)
            .order_by('-created_at')
            .first()
        )

    if contact is None:
        logger.info('[context-lookup] phone lookup: no contact found (records=0)')
        # Echo the queried phone so the caller can still see what was searched.
        return {
            'found': False,
            'match_type': 'phone',
            'contact': {'name': '', 'phone': phone},
            'most_recent': None,
            'known_locations': [],
        }

    jobs = list(
        JobRecord.objects.filter(
            organization=organization, contact=contact,
        ).order_by('-created_at')[:RECENT_JOBS_LIMIT]
    )
    locations = _dedupe_locations([_location_from_job(j, contact) for j in jobs])
    logger.info(
        '[context-lookup] phone lookup: contact=%s jobs=%d locations=%d',
        str(contact.id)[:8], len(jobs), len(locations),
    )
    return _result('phone', True, contact, locations)


def _lookup_by_job_field(organization, field: str, value: str, match_type: str) -> dict:
    jobs = list(
        JobRecord.objects.filter(
            organization=organization, **{f'{field}__icontains': value},
        )
        .select_related('contact')
        .order_by('-created_at')[:RECENT_JOBS_LIMIT]
    )
    if not jobs:
        logger.info('[context-lookup] %s lookup: no records found (records=0)', match_type)
        return _result(match_type, False, None, [])

    locations = _dedupe_locations([_location_from_job(j, j.contact) for j in jobs])
    logger.info('[context-lookup] %s lookup: records=%d locations=%d', match_type, len(jobs), len(locations))
    return _result(match_type, True, jobs[0].contact, locations)
