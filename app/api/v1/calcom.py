"""Cal.com OAuth connect flow + calendar endpoints.

Cal.com only ever redirects the browser back to whatever Redirect URI is registered
on the OAuth client — for a Cal.com Platform OAuth client that's necessarily a
FRONTEND route (e.g. http://localhost:5173/connect-calendar), not a backend one, since
platform OAuth clients are configured with a Website URL + redirect URIs meant for the
app's own UI. So the flow is: frontend redirects to Cal.com -> Cal.com redirects back
to the frontend route with ?code=... -> the frontend (already holding its normal
Authorization header) calls POST /calcom/exchange here to complete it server-side.
That keeps the whole router uniformly auth-gated, unlike a backend-hit callback would.
"""

import secrets
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo, available_timezones

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.auth_deps import get_current_user
from app.api.deps import get_db
from app.core.crypto import encrypt_secret
from app.core.exceptions import ExternalServiceError, NotFoundError, ValidationFailedError
from app.core.logging import get_logger
from app.models.calcom_connection import CalComConnection
from app.models.user import User
from app.repositories.calcom_repository import CalComConnectionRepository
from app.schemas.calcom import (
    BookMeetingRequest,
    CalComAuthorizeUrlOut,
    CalComBookingOut,
    CalComConnectionOut,
    CalComEventTypeOut,
    CalComSlotOut,
    CalComStatusOut,
    CreateEventTypeRequest,
    ExchangeCodeRequest,
    SelectEventTypeRequest,
    SetTimezoneRequest,
    WorkingHoursRequest,
)
from app.services.calcom.client import calcom_client
from app.services.calcom.slots import filter_future_slots
from app.services.calcom.token_service import get_valid_access_token

logger = get_logger(__name__)

router = APIRouter(prefix="/calcom", tags=["calcom"], dependencies=[Depends(get_current_user)])


@router.get("/authorize-url", response_model=CalComAuthorizeUrlOut)
def get_authorize_url() -> CalComAuthorizeUrlOut:
    # The exchange step is itself an authenticated request, so `state` carries no
    # payload — but it is returned alongside the URL so the frontend can stash it
    # (sessionStorage) and verify Cal.com echoes the same value back, rejecting a
    # login-CSRF attempt that would attach an attacker's Cal.com account.
    state = secrets.token_urlsafe(24)
    return CalComAuthorizeUrlOut(authorize_url=calcom_client.build_authorize_url(state=state), state=state)


@router.post("/exchange", response_model=CalComConnectionOut)
def exchange_code(
    payload: ExchangeCodeRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> CalComConnectionOut:
    """Called by the frontend right after Cal.com redirects back to /connect-calendar
    with ?code=... — completes the OAuth exchange and stores the connection."""
    try:
        tokens = calcom_client.exchange_code(payload.code)
        me = calcom_client.get_me(tokens.access_token)
    except ExternalServiceError as exc:
        logger.warning("Cal.com token exchange failed for user %s: %s", user.id, exc.message)
        raise

    repo = CalComConnectionRepository(db)
    existing = repo.get_for_user(user.id)
    fields = dict(
        calcom_user_email=me.get("email"),
        calcom_username=me.get("username"),
        encrypted_access_token=encrypt_secret(tokens.access_token),
        encrypted_refresh_token=encrypt_secret(tokens.refresh_token),
        token_expires_at=tokens.expires_at,
        scope=tokens.scope,
        is_connected=True,
        last_error=None,
    )
    if existing:
        connection = repo.update(existing, **fields)
    else:
        connection = repo.create(CalComConnection(user_id=user.id, timezone="UTC", **fields))

    return CalComConnectionOut.model_validate(connection)


@router.get("/status", response_model=CalComStatusOut)
def get_status(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> CalComStatusOut:
    connection = CalComConnectionRepository(db).get_for_user(user.id)
    if not connection:
        return CalComStatusOut(connected=False, connection=None)
    return CalComStatusOut(
        connected=connection.is_connected, connection=CalComConnectionOut.model_validate(connection)
    )


@router.post("/disconnect", response_model=CalComStatusOut)
def disconnect(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> CalComStatusOut:
    repo = CalComConnectionRepository(db)
    connection = repo.get_for_user(user.id)
    if not connection:
        raise NotFoundError("No Cal.com account connected.")
    repo.delete(connection)
    return CalComStatusOut(connected=False, connection=None)


def _require_connection(db: Session, user: User) -> CalComConnection:
    connection = CalComConnectionRepository(db).get_for_user(user.id)
    if not connection or not connection.is_connected:
        raise NotFoundError("No Cal.com account connected.")
    return connection


def _event_type_out(et: dict) -> CalComEventTypeOut:
    return CalComEventTypeOut(
        id=et["id"],
        title=et["title"],
        slug=et["slug"],
        length_minutes=et.get("lengthInMinutes", 30),
        description=et.get("description"),
        hidden=et.get("hidden"),
        schedule_id=et.get("scheduleId"),
    )


@router.get("/event-types", response_model=list[CalComEventTypeOut])
def list_event_types(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> list[CalComEventTypeOut]:
    _require_connection(db, user)
    access_token = get_valid_access_token(db, user.id)
    raw = calcom_client.list_event_types(access_token)
    return [_event_type_out(et) for et in raw]


@router.post("/event-types", response_model=CalComEventTypeOut)
def create_event_type(
    payload: CreateEventTypeRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> CalComEventTypeOut:
    """Creates a brand new event type on the user's Cal.com account, forwarding every
    field the caller set (see CreateEventTypeRequest — a 1:1 map of Cal.com's own
    create-event-type body). If the caller didn't explicitly set scheduleId and
    working hours have been saved (POST /calcom/working-hours), the event type is
    pointed at that schedule automatically; otherwise Cal.com applies its own default."""
    connection = _require_connection(db, user)
    access_token = get_valid_access_token(db, user.id)

    body = payload.model_dump(by_alias=True, exclude_none=True)
    if "scheduleId" not in body and connection.calcom_schedule_id:
        body["scheduleId"] = connection.calcom_schedule_id

    created = calcom_client.create_event_type(access_token, body=body)
    return _event_type_out(created)


@router.post("/event-type", response_model=CalComConnectionOut)
def select_event_type(
    payload: SelectEventTypeRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> CalComConnectionOut:
    repo = CalComConnectionRepository(db)
    connection = _require_connection(db, user)
    connection = repo.update(
        connection,
        selected_event_type_id=payload.event_type_id,
        selected_event_type_slug=payload.slug,
        selected_event_type_title=payload.title,
    )
    return CalComConnectionOut.model_validate(connection)


@router.post("/timezone", response_model=CalComConnectionOut)
def set_timezone(
    payload: SetTimezoneRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> CalComConnectionOut:
    if payload.timezone not in available_timezones():
        raise ValidationFailedError(f"'{payload.timezone}' is not a recognized IANA timezone.")
    repo = CalComConnectionRepository(db)
    connection = _require_connection(db, user)
    # Keep the Cal.com Schedule's timeZone in step — otherwise slots keep being
    # generated in the old timezone until working hours happen to be re-saved.
    if connection.calcom_schedule_id:
        access_token = get_valid_access_token(db, user.id)
        calcom_client.update_schedule(
            access_token,
            connection.calcom_schedule_id,
            name=connection.calcom_schedule_name or "FocalReach working hours",
            timezone_name=payload.timezone,
            availability=[
                {
                    "days": connection.working_days,
                    "startTime": connection.working_hours_start,
                    "endTime": connection.working_hours_end,
                }
            ],
        )
    connection = repo.update(connection, timezone=payload.timezone)
    return CalComConnectionOut.model_validate(connection)


@router.post("/working-hours", response_model=CalComConnectionOut)
def set_working_hours(
    payload: WorkingHoursRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> CalComConnectionOut:
    """Creates or updates the Cal.com "Schedule" backing this user's working hours.
    New event types (POST /calcom/event-types) automatically point at it afterward."""
    repo = CalComConnectionRepository(db)
    connection = _require_connection(db, user)
    access_token = get_valid_access_token(db, user.id)

    availability = [{"days": payload.days, "startTime": payload.start_time, "endTime": payload.end_time}]
    schedule_name = "FocalReach working hours"

    if connection.calcom_schedule_id:
        schedule = calcom_client.update_schedule(
            access_token,
            connection.calcom_schedule_id,
            name=schedule_name,
            timezone_name=connection.timezone,
            availability=availability,
        )
    else:
        schedule = calcom_client.create_schedule(
            access_token,
            name=schedule_name,
            timezone_name=connection.timezone,
            availability=availability,
            is_default=True,
        )

    connection = repo.update(
        connection,
        working_days=payload.days,
        working_hours_start=payload.start_time,
        working_hours_end=payload.end_time,
        calcom_schedule_id=schedule.get("id", connection.calcom_schedule_id),
        calcom_schedule_name=schedule_name,
    )
    return CalComConnectionOut.model_validate(connection)


@router.get("/slots/upcoming", response_model=list[CalComSlotOut])
def upcoming_slots(
    count: int = Query(default=5, ge=1, le=20),
    days_ahead: int = Query(default=14, ge=1, le=60),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[CalComSlotOut]:
    connection = _require_connection(db, user)
    if not connection.selected_event_type_id:
        raise ValidationFailedError("Select an event type before requesting available slots.")
    access_token = get_valid_access_token(db, user.id)
    now = datetime.now(ZoneInfo(connection.timezone))
    raw_slots = calcom_client.get_slots(
        access_token,
        event_type_id=connection.selected_event_type_id,
        timezone_name=connection.timezone,
        start=now,
        end=now + timedelta(days=days_ahead),
    )
    future = filter_future_slots(raw_slots, now)
    return [CalComSlotOut(start=s["start"], end=s.get("end")) for s in future[:count]]


@router.post("/bookings", response_model=CalComBookingOut)
def book_meeting(
    payload: BookMeetingRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> CalComBookingOut:
    connection = _require_connection(db, user)
    if not connection.selected_event_type_id:
        raise ValidationFailedError("Select an event type before booking a meeting.")
    access_token = get_valid_access_token(db, user.id)
    booking = calcom_client.create_booking(
        access_token,
        event_type_id=connection.selected_event_type_id,
        start=payload.start,
        timezone_name=connection.timezone,
        attendee_name=payload.attendee_name,
        attendee_email=payload.attendee_email,
    )
    return CalComBookingOut(
        id=booking.get("id"),
        uid=booking.get("uid"),
        status=booking.get("status"),
        start=booking.get("start"),
        end=booking.get("end"),
        meeting_url=booking.get("meetingUrl") or booking.get("location"),
    )
