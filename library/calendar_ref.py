"""Resolve a human calendar reference against a list of calendar sources.

Backs the ``[General] default_calendar`` config option, which decides which
calendar an event editor pre-selects (``kubux-calendar-attach``'s import
window and the GUI's new-event dialog).  Resolution is **offline**: it works
on the cached calendar sources, never the server.

Accepted reference forms, tried in this order:

1. full source id      — ``caldav:Nextcloud.Primary:beruflich``, ``ics:Holidays``
2. ``<account>/<cal>`` — ``Nextcloud.Primary/beruflich`` (the ``Nextcloud.``
   prefix on the account is optional)
3. bare calendar name  — ``beruflich``

Forms 2 and 3 match the calendar part against both the calendar's id (the
last segment of its source id) and its display name, case-insensitively.
An exact case-sensitive match wins over a case-insensitive one, and an
ambiguous case-insensitive match counts as **no match** (:data:`None`) so the
caller can report it instead of silently picking one.
"""

from __future__ import annotations

from typing import Optional, Sequence

from library.log import debug_log, Level

_NEXTCLOUD_PREFIX = "nextcloud."


def _norm_account(name: str) -> str:
    """Casefold *name* and drop a leading ``Nextcloud.`` prefix."""
    s = (name or "").strip().casefold()
    if s.startswith(_NEXTCLOUD_PREFIX):
        s = s[len(_NEXTCLOUD_PREFIX):]
    return s


def _split_id(source) -> tuple[str, str]:
    """Return ``(account, calendar)`` for a source's id.

    CalDAV source ids are ``caldav:<account>:<calendar>``; ICS ones are
    ``ics:<name>`` and have no account.
    """
    sid = getattr(source, "id", "") or ""
    parts = sid.split(":")
    if len(parts) >= 3 and parts[0] == "caldav":
        return parts[1], ":".join(parts[2:])
    return "", sid


def _identifiers(source) -> list[str]:
    """Return every string a user could use to name *source*."""
    _, cal = _split_id(source)
    return [n for n in (getattr(source, "name", "") or "", cal) if n]


def match_calendar_index(ref: Optional[str], calendars: Sequence) -> Optional[int]:
    """Return the index in *calendars* named by *ref*, or :data:`None`.

    :data:`None` means "no match": an unknown name, or a bare name that is
    ambiguous.  The caller keeps its own fallback in that case.
    """
    ref = (ref or "").strip()
    if not ref or not calendars:
        return None
    items = list(calendars)

    # 1) full source id.
    for i, src in enumerate(items):
        if (getattr(src, "id", "") or "") == ref:
            return i

    # 2) <account>/<calendar>.
    if "/" in ref:
        want_account, want_cal = ref.split("/", 1)
        account_key = _norm_account(want_account)
        cal_query = want_cal.strip()
        if not cal_query:
            return None
        in_account = [
            (i, src) for i, src in enumerate(items)
            if _norm_account(_split_id(src)[0]) == account_key
        ]
        for i, src in in_account:                      # exact case first
            if cal_query in _identifiers(src):
                return i
        hits = [
            i for i, src in in_account
            if cal_query.casefold() in [n.casefold() for n in _identifiers(src)]
        ]
        return hits[0] if len(hits) == 1 else None

    # 3) bare calendar name / id.
    for i, src in enumerate(items):
        if ref in _identifiers(src):
            return i
    hits = [
        i for i, src in enumerate(items)
        if ref.casefold() in [n.casefold() for n in _identifiers(src)]
    ]
    return hits[0] if len(hits) == 1 else None


def resolve_calendar_id(ref: Optional[str], calendars: Sequence) -> Optional[str]:
    """Return the id of the calendar *ref* names, or :data:`None`.

    Same matching as :func:`match_calendar_index`, plus a WARN log when a
    non-empty *ref* does not resolve — a typo must be visible instead of
    silently landing the event in a different calendar.
    """
    ref = (ref or "").strip()
    if not ref:
        return None
    idx = match_calendar_index(ref, calendars)
    if idx is None:
        available = ", ".join(sorted({c.name or c.id for c in calendars})) or "(none)"
        debug_log(Level.WARN,
                  f"default_calendar {ref!r} matches no writable calendar; "
                  f"available: {available}")
        return None
    return calendars[idx].id
