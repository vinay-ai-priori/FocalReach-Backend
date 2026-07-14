"""Derives a lead's IANA timezone from their country (name or ISO alpha-2 code).
`pycountry` and `pytz` are only needed for this one lookup, so both are imported lazily
to keep them off the app's startup import path."""

from dataclasses import dataclass


@dataclass
class TimezoneResult:
    country: str
    country_code: str | None
    timezone: str | None


def resolve_timezone_for_country(country: str) -> TimezoneResult:
    import pycountry
    import pytz

    country = country.strip()
    country_code = _resolve_country_code(country, pycountry)
    if not country_code:
        return TimezoneResult(country=country, country_code=None, timezone=None)

    timezones = pytz.country_timezones.get(country_code)
    timezone = timezones[0] if timezones else None
    return TimezoneResult(country=country, country_code=country_code, timezone=timezone)


def _resolve_country_code(country: str, pycountry_module) -> str | None:
    if len(country) == 2 and country.isalpha():
        match = pycountry_module.countries.get(alpha_2=country.upper())
        if match:
            return match.alpha_2

    match = pycountry_module.countries.get(name=country)
    if match:
        return match.alpha_2

    try:
        results = pycountry_module.countries.search_fuzzy(country)
    except LookupError:
        return None
    return results[0].alpha_2 if results else None
