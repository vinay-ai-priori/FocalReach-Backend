"""Derives a lead's IANA timezone from their country (name or ISO alpha-2 code).
`pycountry` and `pytz` are only needed for this one lookup, so both are imported lazily
to keep them off the app's startup import path."""

from dataclasses import dataclass


@dataclass
class TimezoneResult:
    country: str
    country_code: str | None
    timezone: str | None


# Common informal / abbreviated country names CSV imports actually contain, which
# pycountry either misses outright ("UAE", "Turkey") or — worse — fuzzy-matches to the
# wrong country ("UK" -> Uganda, "Korea" -> North Korea). Keys are normalized:
# uppercased, periods stripped, whitespace collapsed.
_COUNTRY_ALIASES: dict[str, str] = {
    "UK": "GB",
    "GREAT BRITAIN": "GB",
    "ENGLAND": "GB",
    "SCOTLAND": "GB",
    "WALES": "GB",
    "NORTHERN IRELAND": "GB",
    "USA": "US",
    "US": "US",
    "UNITED STATES OF AMERICA": "US",
    "AMERICA": "US",
    "UAE": "AE",
    "KOREA": "KR",  # business leads overwhelmingly mean South Korea
    "SOUTH KOREA": "KR",
    "NORTH KOREA": "KP",
    "TURKEY": "TR",  # pycountry only knows "Türkiye"
    "IVORY COAST": "CI",  # pycountry only knows "Côte d'Ivoire"
    "RUSSIA": "RU",
    "VIETNAM": "VN",
}

# pytz.country_timezones lists a country's zones in an arbitrary-looking order whose
# first entry can be a fringe zone (Australia -> Lord Howe Island, Canada ->
# Newfoundland, Brazil -> Noronha, Russia -> Kaliningrad). For multi-zone countries,
# prefer the zone where the bulk of business contacts actually are.
_PRIMARY_TIMEZONES: dict[str, str] = {
    "US": "America/New_York",
    "CA": "America/Toronto",
    "AU": "Australia/Sydney",
    "BR": "America/Sao_Paulo",
    "RU": "Europe/Moscow",
    "MX": "America/Mexico_City",
    "ID": "Asia/Jakarta",
    "CN": "Asia/Shanghai",
}


def _normalize(country: str) -> str:
    return " ".join(country.replace(".", "").upper().split())


def resolve_timezone_for_country(country: str) -> TimezoneResult:
    import pycountry
    import pytz

    country = country.strip()
    if not country:
        # Guard: pycountry's fuzzy search "matches" empty/whitespace input to a real
        # country instead of failing — an empty cell must resolve to nothing.
        return TimezoneResult(country=country, country_code=None, timezone=None)

    country_code = _COUNTRY_ALIASES.get(_normalize(country)) or _resolve_country_code(country, pycountry)
    if not country_code:
        return TimezoneResult(country=country, country_code=None, timezone=None)

    timezone = _PRIMARY_TIMEZONES.get(country_code)
    if not timezone:
        timezones = pytz.country_timezones.get(country_code)
        timezone = timezones[0] if timezones else None
    return TimezoneResult(country=country, country_code=country_code, timezone=timezone)


def _resolve_country_code(country: str, pycountry_module) -> str | None:
    if len(country) == 2 and country.isalpha():
        match = pycountry_module.countries.get(alpha_2=country.upper())
        # A 2-letter string is an alpha-2 code attempt — never fuzzy-search it
        # (fuzzy turns unknown codes into unrelated countries, e.g. "UK" -> Uganda).
        return match.alpha_2 if match else None

    match = pycountry_module.countries.get(name=country)
    if match:
        return match.alpha_2

    try:
        results = pycountry_module.countries.search_fuzzy(country)
    except LookupError:
        return None
    return results[0].alpha_2 if results else None
