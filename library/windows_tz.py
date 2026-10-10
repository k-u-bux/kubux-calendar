"""Windows/Exchange time zone keys mapped to standard (IANA) names.

Outlook, Exchange and OWA stamp iCalendar documents with Windows zone keys
("W. Europe Standard Time") instead of IANA names, and such documents reach
this application through paths that never involve a mail client: an Exchange
"share calendar" feed subscribed as a read-only ICS source, an `.ics` exported
from Outlook, or an event an Outlook-side client pushed into a CalDAV server,
where the key then sits verbatim and other clients show shifted times.

The key is legal as an iCalendar TZID only when the document also ships the
matching VTIMEZONE block — which Exchange does not always do — and it cannot be
looked up in the tz database by name.  This module maps it to the standard
name, so that displays, edits and everything uploaded to a server carry a name
every calendar application can resolve.

`icalendar` (already a dependency) resolves such keys for *arithmetic*: its
`TZP.timezone()` falls back to its own Windows table, so a Windows-keyed event
already gets the right offset on read.  It hands the key back unchanged to
anyone asking for the *name*, and it is no help to the `pytz`-based code paths,
which is what this module is for.

Table source: CLDR ``common/supplemental/windowsZones.xml``, the
``territory="001"`` rows — the reference Windows -> tzdb mapping, the same data
ICU is generated from:

    https://raw.githubusercontent.com/unicode-org/cldr/main/common/supplemental/windowsZones.xml

139 keys.  Zones renamed in tzdata since that revision were modernised against
tzdata's link table, but only within the same area (a rename is a spelling fix;
a merger such as Atlantic/Reykjavik -> Africa/Abidjan is not chased — the CLDR
name is itself a valid tzdb name).
"""

import re

import pytz

from library.log import debug_log, Level


# iCalendar TZID occurrences: the TZID property of a VTIMEZONE block
# ("TZID:W. Europe Standard Time") and the TZID parameter of DTSTART/DTEND
# (";TZID=W. Europe Standard Time"), whose value may be quoted.
TZID_PROPERTY_RE = re.compile(r'^(TZID:)(\"?)([^":;]*)\2\s*$')
TZID_PARAMETER_RE = re.compile(r'(;TZID=)(\"?)([^":;]*)\2')


# Windows/Exchange time zone keys -> standard (IANA) time zone names.
WINDOWS_TO_IANA = {
    "AUS Central Standard Time": "Australia/Darwin",
    "AUS Eastern Standard Time": "Australia/Sydney",
    "Afghanistan Standard Time": "Asia/Kabul",
    "Alaskan Standard Time": "America/Anchorage",
    "Aleutian Standard Time": "America/Adak",
    "Altai Standard Time": "Asia/Barnaul",
    "Arab Standard Time": "Asia/Riyadh",
    "Arabian Standard Time": "Asia/Dubai",
    "Arabic Standard Time": "Asia/Baghdad",
    "Argentina Standard Time": "America/Argentina/Buenos_Aires",
    "Astrakhan Standard Time": "Europe/Astrakhan",
    "Atlantic Standard Time": "America/Halifax",
    "Aus Central W. Standard Time": "Australia/Eucla",
    "Azerbaijan Standard Time": "Asia/Baku",
    "Azores Standard Time": "Atlantic/Azores",
    "Bahia Standard Time": "America/Bahia",
    "Bangladesh Standard Time": "Asia/Dhaka",
    "Belarus Standard Time": "Europe/Minsk",
    "Bougainville Standard Time": "Pacific/Bougainville",
    "Canada Central Standard Time": "America/Regina",
    "Cape Verde Standard Time": "Atlantic/Cape_Verde",
    "Caucasus Standard Time": "Asia/Yerevan",
    "Cen. Australia Standard Time": "Australia/Adelaide",
    "Central America Standard Time": "America/Guatemala",
    "Central Asia Standard Time": "Asia/Bishkek",
    "Central Brazilian Standard Time": "America/Cuiaba",
    "Central Europe Standard Time": "Europe/Budapest",
    "Central European Standard Time": "Europe/Warsaw",
    "Central Pacific Standard Time": "Pacific/Guadalcanal",
    "Central Standard Time": "America/Chicago",
    "Central Standard Time (Mexico)": "America/Mexico_City",
    "Chatham Islands Standard Time": "Pacific/Chatham",
    "China Standard Time": "Asia/Shanghai",
    "Cuba Standard Time": "America/Havana",
    "Dateline Standard Time": "Etc/GMT+12",
    "E. Africa Standard Time": "Africa/Nairobi",
    "E. Australia Standard Time": "Australia/Brisbane",
    "E. Europe Standard Time": "Europe/Chisinau",
    "E. South America Standard Time": "America/Sao_Paulo",
    "Easter Island Standard Time": "Pacific/Easter",
    "Eastern Standard Time": "America/New_York",
    "Eastern Standard Time (Mexico)": "America/Cancun",
    "Egypt Standard Time": "Africa/Cairo",
    "Ekaterinburg Standard Time": "Asia/Yekaterinburg",
    "FLE Standard Time": "Europe/Kyiv",
    "Fiji Standard Time": "Pacific/Fiji",
    "GMT Standard Time": "Europe/London",
    "GTB Standard Time": "Europe/Bucharest",
    "Georgian Standard Time": "Asia/Tbilisi",
    "Greenland Standard Time": "America/Nuuk",
    "Greenwich Standard Time": "Atlantic/Reykjavik",
    "Haiti Standard Time": "America/Port-au-Prince",
    "Hawaiian Standard Time": "Pacific/Honolulu",
    "India Standard Time": "Asia/Kolkata",
    "Iran Standard Time": "Asia/Tehran",
    "Israel Standard Time": "Asia/Jerusalem",
    "Jordan Standard Time": "Asia/Amman",
    "Kaliningrad Standard Time": "Europe/Kaliningrad",
    "Korea Standard Time": "Asia/Seoul",
    "Libya Standard Time": "Africa/Tripoli",
    "Line Islands Standard Time": "Pacific/Kiritimati",
    "Lord Howe Standard Time": "Australia/Lord_Howe",
    "Magadan Standard Time": "Asia/Magadan",
    "Magallanes Standard Time": "America/Punta_Arenas",
    "Marquesas Standard Time": "Pacific/Marquesas",
    "Mauritius Standard Time": "Indian/Mauritius",
    "Middle East Standard Time": "Asia/Beirut",
    "Montevideo Standard Time": "America/Montevideo",
    "Morocco Standard Time": "Africa/Casablanca",
    "Mountain Standard Time": "America/Denver",
    "Mountain Standard Time (Mexico)": "America/Mazatlan",
    "Myanmar Standard Time": "Asia/Yangon",
    "N. Central Asia Standard Time": "Asia/Novosibirsk",
    "Namibia Standard Time": "Africa/Windhoek",
    "Nepal Standard Time": "Asia/Kathmandu",
    "New Zealand Standard Time": "Pacific/Auckland",
    "Newfoundland Standard Time": "America/St_Johns",
    "Norfolk Standard Time": "Pacific/Norfolk",
    "North Asia East Standard Time": "Asia/Irkutsk",
    "North Asia Standard Time": "Asia/Krasnoyarsk",
    "North Korea Standard Time": "Asia/Pyongyang",
    "Omsk Standard Time": "Asia/Omsk",
    "Pacific SA Standard Time": "America/Santiago",
    "Pacific Standard Time": "America/Los_Angeles",
    "Pacific Standard Time (Mexico)": "America/Tijuana",
    "Pakistan Standard Time": "Asia/Karachi",
    "Paraguay Standard Time": "America/Asuncion",
    "Qyzylorda Standard Time": "Asia/Qyzylorda",
    "Romance Standard Time": "Europe/Paris",
    "Russia Time Zone 10": "Asia/Srednekolymsk",
    "Russia Time Zone 11": "Asia/Kamchatka",
    "Russia Time Zone 3": "Europe/Samara",
    "Russian Standard Time": "Europe/Moscow",
    "SA Eastern Standard Time": "America/Cayenne",
    "SA Pacific Standard Time": "America/Bogota",
    "SA Western Standard Time": "America/La_Paz",
    "SE Asia Standard Time": "Asia/Bangkok",
    "Saint Pierre Standard Time": "America/Miquelon",
    "Sakhalin Standard Time": "Asia/Sakhalin",
    "Samoa Standard Time": "Pacific/Apia",
    "Sao Tome Standard Time": "Africa/Sao_Tome",
    "Saratov Standard Time": "Europe/Saratov",
    "Singapore Standard Time": "Asia/Singapore",
    "South Africa Standard Time": "Africa/Johannesburg",
    "South Sudan Standard Time": "Africa/Juba",
    "Sri Lanka Standard Time": "Asia/Colombo",
    "Sudan Standard Time": "Africa/Khartoum",
    "Syria Standard Time": "Asia/Damascus",
    "Taipei Standard Time": "Asia/Taipei",
    "Tasmania Standard Time": "Australia/Hobart",
    "Tocantins Standard Time": "America/Araguaina",
    "Tokyo Standard Time": "Asia/Tokyo",
    "Tomsk Standard Time": "Asia/Tomsk",
    "Tonga Standard Time": "Pacific/Tongatapu",
    "Transbaikal Standard Time": "Asia/Chita",
    "Turkey Standard Time": "Europe/Istanbul",
    "Turks And Caicos Standard Time": "America/Grand_Turk",
    "US Eastern Standard Time": "America/Indiana/Indianapolis",
    "US Mountain Standard Time": "America/Phoenix",
    "UTC": "Etc/UTC",
    "UTC+12": "Etc/GMT-12",
    "UTC+13": "Etc/GMT-13",
    "UTC-02": "Etc/GMT+2",
    "UTC-08": "Etc/GMT+8",
    "UTC-09": "Etc/GMT+9",
    "UTC-11": "Etc/GMT+11",
    "Ulaanbaatar Standard Time": "Asia/Ulaanbaatar",
    "Venezuela Standard Time": "America/Caracas",
    "Vladivostok Standard Time": "Asia/Vladivostok",
    "Volgograd Standard Time": "Europe/Volgograd",
    "W. Australia Standard Time": "Australia/Perth",
    "W. Central Africa Standard Time": "Africa/Lagos",
    "W. Europe Standard Time": "Europe/Berlin",
    "W. Mongolia Standard Time": "Asia/Hovd",
    "West Asia Standard Time": "Asia/Tashkent",
    "West Bank Standard Time": "Asia/Hebron",
    "West Pacific Standard Time": "Pacific/Port_Moresby",
    "Yakutsk Standard Time": "Asia/Yakutsk",
    "Yukon Standard Time": "America/Whitehorse",
}


def _icu_first_iana(key):
    """First standard name Qt/ICU knows for *key*, or None.

    Each candidate has the same offsets as the key, so the first one is good
    enough for display.  Qt is optional here: the CLI tools run without a GUI
    (and without Qt), where this simply yields None.
    """
    try:
        from PySide6.QtCore import QTimeZone
    except Exception:
        return None
    try:
        ids = QTimeZone.windowsIdToIanaIds(key.encode("utf-8"))
    except Exception:
        return None
    return bytes(ids[0]).decode("utf-8") if ids else None


def standard_timezone_name(tzid):
    """Map a time zone id to its standard (IANA) name.

    A Windows key ("W. Europe Standard Time") becomes "Europe/Berlin"; an id
    that already looks standard (it carries a "/") is returned as it is, and an
    empty one stays empty.  A key unknown to the table falls back to whatever
    Qt/ICU knows for it, and to the key itself as a last resort.
    """
    key = (tzid or "").strip().strip('"')
    if not key or "/" in key:
        return key
    name = WINDOWS_TO_IANA.get(key)
    if name:
        return name
    return _icu_first_iana(key) or key


def timezone_for(tzid):
    """Return a `pytz` timezone for *tzid*, or None when it is unknown.

    Handles both standard names and Windows keys; "UTC" and None/empty (a
    floating time) are the caller's business, not this function's.
    """
    name = standard_timezone_name(tzid)
    if not name:
        return None
    try:
        return pytz.timezone(name)
    except Exception:
        debug_log(Level.WARN, f"unknown time zone id {tzid!r}")
        return None


def _unfold_ical_lines(text):
    """Split an iCalendar text into content lines, undoing RFC 5545 folding.

    A long line is folded by inserting CRLF plus one space or tab; the
    continuation is glued back onto the previous line without that character.
    """
    lines = []
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if raw[:1] in (" ", "\t") and lines:
            lines[-1] += raw[1:]
        else:
            lines.append(raw)
    return lines


def _fold_ical_line(line, limit=75):
    """Fold one content line to *limit* octets (RFC 5545 §3.1).

    Counts octets, not characters, and never splits a multi-byte character; a
    continuation line carries one leading space, which counts towards the limit
    too.  Short lines are returned unchanged.
    """
    if len(line.encode("utf-8")) <= limit:
        return line
    chunks = []
    current = ""
    used = 0
    for char in line:
        size = len(char.encode("utf-8"))
        if used + size > limit - (1 if chunks else 0):
            chunks.append(current)
            current = ""
            used = 0
        current += char
        used += size
    chunks.append(current)
    return "\r\n ".join(chunks)


def _carries_windows_key(line):
    """True if *line* holds a TZID the mapping would rewrite."""
    for regex in (TZID_PROPERTY_RE, TZID_PARAMETER_RE):
        match = regex.search(line)
        if match and standard_timezone_name(match.group(3)) != match.group(3):
            return True
    return False


def standardize_ics_timezones(ics_text):
    """Rewrite Windows/Exchange zone keys in an iCalendar text.

    Renames the TZID property of the VTIMEZONE blocks and the TZID parameters
    of DTSTART/DTEND alike, so the document stays self-consistent and every
    calendar application can resolve the zones by name.  Applied to a document
    on its way out (queued for a CalDAV push, uploaded by a tool): a key that
    reaches the server is stored verbatim there and then mis-displayed by every
    client that has no Windows mapping.

    A document that carries no such key is returned unchanged, byte for byte —
    the rewrite is not applied merely because it could be.  When a rewrite does
    happen the lines are unfolded first and refolded after (a TZID may be
    folded across lines), which is why untouched long lines come back refolded.
    """
    def rename(match):
        quote = match.group(2)
        return f"{match.group(1)}{quote}{standard_timezone_name(match.group(3))}{quote}"

    lines = _unfold_ical_lines(ics_text)
    while lines and not lines[-1]:
        lines.pop()
    if not any(_carries_windows_key(line) for line in lines):
        return ics_text
    folded = []
    for line in lines:
        line = TZID_PROPERTY_RE.sub(rename, line)
        line = TZID_PARAMETER_RE.sub(rename, line)
        folded.append(_fold_ical_line(line))
    return "\r\n".join(folded) + "\r\n"
