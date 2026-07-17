"""Thin wrapper around Cal.com's OAuth + v2 API. Every call raises ExternalServiceError
on a non-2xx response OR a transport failure (timeout, DNS, connection reset), so
routers never see raw httpx exceptions. The upstream HTTP status (when there was a
response at all) rides along on the error as `upstream_status` — the token service
uses it to tell "Cal.com rejected this token" (4xx, disconnect) apart from "Cal.com
is having a bad day" (5xx/network, keep the connection and retry later)."""

from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from app.core.config import settings
from app.core.exceptions import ExternalServiceError


class CalComTokens:
    def __init__(self, access_token: str, refresh_token: str | None, expires_at: datetime, scope: str | None):
        self.access_token = access_token
        # None means the provider response carried no refresh_token — callers keep
        # whatever refresh token they already hold rather than storing a bogus one.
        self.refresh_token = refresh_token
        self.expires_at = expires_at
        self.scope = scope


def _tokens_from_response(payload: dict[str, Any]) -> CalComTokens:
    expires_in = payload.get("expires_in")
    if expires_in is not None:
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=int(expires_in))
    elif payload.get("expires_at"):
        expires_at = datetime.fromtimestamp(int(payload["expires_at"]), tz=timezone.utc)
    else:
        # Conservative fallback so a provider response missing both fields never
        # produces a token we treat as permanently valid.
        expires_at = datetime.now(timezone.utc) + timedelta(minutes=30)
    return CalComTokens(
        access_token=payload["access_token"],
        refresh_token=payload.get("refresh_token") or None,
        expires_at=expires_at,
        scope=payload.get("scope"),
    )


def _request(method: str, url: str, *, error_prefix: str, **kwargs: Any) -> httpx.Response:
    """One choke point for every Cal.com HTTP call: transport failures and non-2xx
    responses both surface as ExternalServiceError (with upstream_status set when a
    response was actually received)."""
    try:
        resp = httpx.request(method, url, **kwargs)
    except httpx.HTTPError as exc:
        raise ExternalServiceError(f"{error_prefix}: could not reach Cal.com ({exc.__class__.__name__})") from exc
    if resp.status_code >= 400:
        error = ExternalServiceError(f"{error_prefix}: {resp.text}")
        error.upstream_status = resp.status_code
        raise error
    return resp


class CalComClient:
    def __init__(self) -> None:
        self._auth = (settings.CALCOM_CLIENT_ID, settings.CALCOM_CLIENT_SECRET)

    def build_authorize_url(self, *, state: str) -> str:
        # Only the params Cal.com's OAuth docs actually accept (client_id,
        # redirect_uri, scope, state) plus the spec-standard response_type. No
        # `prompt`: Cal.com doesn't document it and demonstrably ignores account
        # switching — connecting a different account requires signing out at
        # app.cal.com first (surfaced in the connect UI).
        params = httpx.QueryParams(
            {
                "client_id": settings.CALCOM_CLIENT_ID,
                "redirect_uri": settings.CALCOM_REDIRECT_URI,
                "response_type": "code",
                "scope": settings.CALCOM_OAUTH_SCOPES,
                "state": state,
            }
        )
        return f"{settings.CALCOM_OAUTH_AUTHORIZE_URL}?{params}"

    def exchange_code(self, code: str) -> CalComTokens:
        resp = _request(
            "POST",
            settings.CALCOM_OAUTH_TOKEN_URL,
            error_prefix="Cal.com rejected the authorization code",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": settings.CALCOM_REDIRECT_URI,
                "client_id": settings.CALCOM_CLIENT_ID,
                "client_secret": settings.CALCOM_CLIENT_SECRET,
            },
            timeout=15,
        )
        tokens = _tokens_from_response(resp.json())
        if not tokens.refresh_token:
            # On the initial exchange there is no previous refresh token to fall back
            # on — a connection stored without one could never be refreshed.
            raise ExternalServiceError("Cal.com token response did not include a refresh token.")
        return tokens

    def refresh_token(self, refresh_token: str) -> CalComTokens:
        resp = _request(
            "POST",
            settings.CALCOM_OAUTH_TOKEN_URL,
            error_prefix="Cal.com refused to refresh the token",
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": settings.CALCOM_CLIENT_ID,
                "client_secret": settings.CALCOM_CLIENT_SECRET,
            },
            timeout=15,
        )
        return _tokens_from_response(resp.json())

    # Cal.com v2 is date-versioned PER ENDPOINT, not globally — sending the wrong
    # version for a given route silently falls back to an older response shape rather
    # than erroring, which is what made the event-types list come back empty. Values
    # below are each endpoint's documented required version (cal.com/docs/api-reference).
    _VERSION_ME = "2024-08-13"
    _VERSION_EVENT_TYPES = "2024-06-14"
    _VERSION_SLOTS = "2024-09-04"
    _VERSION_BOOKINGS = "2026-02-25"
    _VERSION_SCHEDULES = "2024-06-11"

    def _headers(self, access_token: str, api_version: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {access_token}", "cal-api-version": api_version}

    def get_me(self, access_token: str) -> dict[str, Any]:
        resp = _request(
            "GET",
            f"{settings.CALCOM_API_BASE_URL}/me",
            error_prefix="Could not fetch Cal.com profile",
            headers=self._headers(access_token, self._VERSION_ME),
            timeout=15,
        )
        return resp.json().get("data", resp.json())

    def get_event_type(self, access_token: str, event_type_id: int) -> dict[str, Any]:
        resp = _request(
            "GET",
            f"{settings.CALCOM_API_BASE_URL}/event-types/{event_type_id}",
            error_prefix="Could not fetch the Cal.com event type",
            headers=self._headers(access_token, self._VERSION_EVENT_TYPES),
            timeout=15,
        )
        return resp.json().get("data", resp.json())

    def create_event_type(self, access_token: str, *, body: dict[str, Any]) -> dict[str, Any]:
        """`body` is the exact Cal.com request body (see schemas.calcom.
        CreateEventTypeRequest.model_dump(by_alias=True, exclude_none=True)) —
        forwarded as-is so every field the endpoint supports is reachable."""
        resp = _request(
            "POST",
            f"{settings.CALCOM_API_BASE_URL}/event-types",
            error_prefix="Could not create Cal.com event type",
            headers=self._headers(access_token, self._VERSION_EVENT_TYPES),
            json=body,
            timeout=15,
        )
        return resp.json().get("data", resp.json())

    def update_event_type(self, access_token: str, event_type_id: int, *, body: dict[str, Any]) -> dict[str, Any]:
        """`body` is the exact Cal.com PATCH request body (see schemas.calcom.
        UpdateEventTypeRequest) — forwarded as-is. Slug is deliberately never part of
        this body (see UpdateEventTypeRequest) so the booking link never changes."""
        resp = _request(
            "PATCH",
            f"{settings.CALCOM_API_BASE_URL}/event-types/{event_type_id}",
            error_prefix="Could not update Cal.com event type",
            headers=self._headers(access_token, self._VERSION_EVENT_TYPES),
            json=body,
            timeout=15,
        )
        return resp.json().get("data", resp.json())

    def create_schedule(
        self, access_token: str, *, name: str, timezone_name: str, availability: list[dict[str, Any]], is_default: bool
    ) -> dict[str, Any]:
        resp = _request(
            "POST",
            f"{settings.CALCOM_API_BASE_URL}/schedules",
            error_prefix="Could not create Cal.com working-hours schedule",
            headers=self._headers(access_token, self._VERSION_SCHEDULES),
            json={"name": name, "timeZone": timezone_name, "isDefault": is_default, "availability": availability},
            timeout=15,
        )
        return resp.json().get("data", resp.json())

    def update_schedule(
        self,
        access_token: str,
        schedule_id: int,
        *,
        name: str,
        timezone_name: str,
        availability: list[dict[str, Any]],
    ) -> dict[str, Any]:
        resp = _request(
            "PATCH",
            f"{settings.CALCOM_API_BASE_URL}/schedules/{schedule_id}",
            error_prefix="Could not update Cal.com working-hours schedule",
            headers=self._headers(access_token, self._VERSION_SCHEDULES),
            json={"name": name, "timeZone": timezone_name, "availability": availability},
            timeout=15,
        )
        return resp.json().get("data", resp.json())

    def get_slots(
        self, access_token: str, *, event_type_id: int, timezone_name: str, start: datetime, end: datetime
    ) -> list[dict[str, Any]]:
        resp = _request(
            "GET",
            f"{settings.CALCOM_API_BASE_URL}/slots",
            error_prefix="Could not fetch Cal.com availability",
            headers=self._headers(access_token, self._VERSION_SLOTS),
            params={
                "eventTypeId": event_type_id,
                "start": start.date().isoformat(),
                "end": end.date().isoformat(),
                "timeZone": timezone_name,
            },
            timeout=20,
        )
        data = resp.json().get("data", {})
        slots: list[dict[str, Any]] = []
        for day_slots in data.values():
            slots.extend(day_slots)
        slots.sort(key=lambda s: s.get("start", ""))
        return slots

    def create_booking(
        self,
        access_token: str,
        *,
        event_type_id: int,
        start: str,
        timezone_name: str,
        attendee_name: str,
        attendee_email: str,
    ) -> dict[str, Any]:
        resp = _request(
            "POST",
            f"{settings.CALCOM_API_BASE_URL}/bookings",
            error_prefix="Could not book the Cal.com meeting",
            headers=self._headers(access_token, self._VERSION_BOOKINGS),
            json={
                "eventTypeId": event_type_id,
                "start": start,
                "attendee": {"name": attendee_name, "email": attendee_email, "timeZone": timezone_name},
            },
            timeout=20,
        )
        return resp.json().get("data", resp.json())


calcom_client = CalComClient()
