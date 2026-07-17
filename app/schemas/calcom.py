import re
from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator

VALID_DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
_TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


class CalComAuthorizeUrlOut(BaseModel):
    authorize_url: str
    # Echoed back by Cal.com in the redirect — the frontend stores it before
    # redirecting and verifies the round-trip (OAuth CSRF protection).
    state: str


class ExchangeCodeRequest(BaseModel):
    code: str = Field(min_length=1)


class CalComConnectionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    public_id: UUID
    calcom_user_email: str | None = None
    calcom_username: str | None = None
    is_connected: bool
    last_error: str | None = None
    timezone: str
    selected_event_type_id: int | None = None
    selected_event_type_slug: str | None = None
    selected_event_type_title: str | None = None
    working_days: list[str]
    working_hours_start: str
    working_hours_end: str
    calcom_schedule_id: int | None = None
    calcom_schedule_name: str | None = None
    token_expires_at: datetime
    created_at: datetime


class CalComStatusOut(BaseModel):
    connected: bool
    connection: CalComConnectionOut | None = None


class CalComEventTypeDetailOut(BaseModel):
    """The current live values of the user's one event type — fetched fresh from
    Cal.com so the edit form never silently resets a field the UI didn't ask about
    (we don't persist length/description/notice locally, only title/slug/id)."""

    title: str
    slug: str
    length_minutes: int
    description: str | None = None
    minimum_booking_notice: int | None = None


class CreateEventTypeRequest(BaseModel):
    """Maps 1:1 onto Cal.com's POST /v2/event-types body (cal.com/docs/api-reference/
    v2/event-types/create-an-event-type) — field names below are the exact Cal.com
    camelCase names via alias, so what you send here is what Cal.com receives, with no
    lossy translation. Complex nested fields (locations, bookingFields, bookingWindow,
    etc.) are typed as loose dicts/lists since Cal.com's own union schemas for them are
    validated server-side; malformed shapes come back as a clear Cal.com 400, not a
    silent drop. `exclude_none` on dump means unset fields are simply omitted, letting
    Cal.com apply its own defaults.

    A user may only ever create ONE event type (see POST /calcom/event-type) — this is
    the create-time body. To change it afterward, see UpdateEventTypeRequest below."""

    model_config = ConfigDict(populate_by_name=True)

    # --- Core (required) ---
    title: str = Field(min_length=1, max_length=255)
    slug: str = Field(min_length=1, max_length=255)
    length_in_minutes: int = Field(gt=0, le=1440, alias="lengthInMinutes")

    # --- Core (optional) ---
    description: str | None = Field(default=None, max_length=2000)
    length_in_minutes_options: list[int] | None = Field(default=None, alias="lengthInMinutesOptions")
    locations: list[dict[str, Any]] | None = None
    booking_fields: list[dict[str, Any]] | None = Field(default=None, alias="bookingFields")

    # --- Scheduling / availability ---
    schedule_id: int | None = Field(default=None, alias="scheduleId")
    minimum_booking_notice: int | None = Field(default=None, ge=0, alias="minimumBookingNotice")
    before_event_buffer: int | None = Field(default=None, ge=0, alias="beforeEventBuffer")
    after_event_buffer: int | None = Field(default=None, ge=0, alias="afterEventBuffer")
    slot_interval: int | None = Field(default=None, gt=0, alias="slotInterval")
    offset_start: int | None = Field(default=None, alias="offsetStart")
    only_show_first_available_slot: bool | None = Field(default=None, alias="onlyShowFirstAvailableSlot")
    booking_window: dict[str, Any] | None = Field(default=None, alias="bookingWindow")
    booking_limits_count: dict[str, int] | None = Field(default=None, alias="bookingLimitsCount")
    booking_limits_duration: dict[str, int] | None = Field(default=None, alias="bookingLimitsDuration")
    booker_active_bookings_limit: dict[str, Any] | None = Field(default=None, alias="bookerActiveBookingsLimit")
    booker_layouts: dict[str, Any] | None = Field(default=None, alias="bookerLayouts")

    # --- Guests / confirmation / booker verification ---
    disable_guests: bool | None = Field(default=None, alias="disableGuests")
    requires_booker_email_verification: bool | None = Field(
        default=None, alias="requiresBookerEmailVerification"
    )
    booking_requires_authentication: bool | None = Field(default=None, alias="bookingRequiresAuthentication")
    confirmation_policy: dict[str, Any] | None = Field(default=None, alias="confirmationPolicy")

    # --- Recurrence / seats ---
    recurrence: dict[str, Any] | None = None
    seats: dict[str, Any] | None = None

    # --- Display / calendar behavior ---
    hidden: bool | None = None
    color: dict[str, str] | None = None
    custom_name: str | None = Field(default=None, alias="customName")
    hide_calendar_notes: bool | None = Field(default=None, alias="hideCalendarNotes")
    hide_calendar_event_details: bool | None = Field(default=None, alias="hideCalendarEventDetails")
    hide_organizer_email: bool | None = Field(default=None, alias="hideOrganizerEmail")
    lock_time_zone_toggle_on_booking_page: bool | None = Field(
        default=None, alias="lockTimeZoneToggleOnBookingPage"
    )
    interface_language: str | None = Field(default=None, alias="interfaceLanguage")
    destination_calendar: dict[str, Any] | None = Field(default=None, alias="destinationCalendar")
    use_destination_calendar_email: bool | None = Field(default=None, alias="useDestinationCalendarEmail")
    cal_video_settings: dict[str, Any] | None = Field(default=None, alias="calVideoSettings")

    # --- Post-booking behavior ---
    success_redirect_url: str | None = Field(default=None, alias="successRedirectUrl")
    disable_cancelling: dict[str, Any] | bool | None = Field(default=None, alias="disableCancelling")
    disable_rescheduling: dict[str, Any] | bool | None = Field(default=None, alias="disableRescheduling")
    allow_rescheduling_past_bookings: bool | None = Field(default=None, alias="allowReschedulingPastBookings")
    allow_rescheduling_cancelled_bookings: bool | None = Field(
        default=None, alias="allowReschedulingCancelledBookings"
    )
    show_optimized_slots: bool | None = Field(default=None, alias="showOptimizedSlots")

    # --- Private organizer notes ---
    private_note_enabled: bool | None = Field(default=None, alias="privateNoteEnabled")
    private_note_mode: str | None = Field(default=None, alias="privateNoteMode")
    private_note_template: str | None = Field(default=None, alias="privateNoteTemplate")


class UpdateEventTypeRequest(BaseModel):
    """Editable subset of Cal.com's event-type fields for the user's one-and-only
    event type (see CreateEventTypeRequest for the full create-time superset). `slug`
    is deliberately NOT editable here — changing it would silently break the booking
    link already shown on the Discovery Calls page and sent to leads."""

    model_config = ConfigDict(populate_by_name=True)

    title: str | None = Field(default=None, min_length=1, max_length=255)
    length_in_minutes: int | None = Field(default=None, gt=0, le=1440, alias="lengthInMinutes")
    description: str | None = Field(default=None, max_length=2000)
    minimum_booking_notice: int | None = Field(default=None, ge=0, alias="minimumBookingNotice")

    @model_validator(mode="after")
    def _at_least_one_field(self) -> "UpdateEventTypeRequest":
        if all(
            v is None
            for v in (self.title, self.length_in_minutes, self.description, self.minimum_booking_notice)
        ):
            raise ValueError("Provide at least one field to update.")
        return self


class SetTimezoneRequest(BaseModel):
    timezone: str = Field(min_length=1, max_length=64)


class WorkingHoursRequest(BaseModel):
    days: list[str] = Field(min_length=1)
    start_time: str
    end_time: str

    @field_validator("days")
    @classmethod
    def _validate_days(cls, value: list[str]) -> list[str]:
        invalid = [d for d in value if d not in VALID_DAYS]
        if invalid:
            raise ValueError(f"Invalid day(s): {', '.join(invalid)}. Must be one of {', '.join(VALID_DAYS)}.")
        return value

    @field_validator("start_time", "end_time")
    @classmethod
    def _validate_time(cls, value: str) -> str:
        if not _TIME_RE.match(value):
            raise ValueError("Time must be in 24-hour HH:MM format, e.g. '09:00'.")
        return value

    @model_validator(mode="after")
    def _validate_range(self) -> "WorkingHoursRequest":
        if self.start_time >= self.end_time:
            raise ValueError("start_time must be before end_time.")
        return self


class CalComSlotOut(BaseModel):
    start: str
    end: str | None = None


class BookMeetingRequest(BaseModel):
    start: str
    attendee_name: str = Field(min_length=1, max_length=255)
    attendee_email: EmailStr


class CalComBookingOut(BaseModel):
    id: int | str | None = None
    uid: str | None = None
    status: str | None = None
    start: str | None = None
    end: str | None = None
    meeting_url: str | None = None
