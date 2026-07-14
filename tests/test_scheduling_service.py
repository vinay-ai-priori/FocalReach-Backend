"""Edge-case gauntlet for the outreach dispatch engine's pure logic
(snap_to_business_hours + find_free_slot). Runs under pytest OR standalone:

    python tests/test_scheduling_service.py
"""

import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.scheduling_service import (  # noqa: E402
    BUSINESS_DAYS,
    BUSINESS_WINDOWS,
    SCHEDULE_GAP,
    SEND_GAP,
    find_free_slot,
    snap_to_business_hours,
)

UTC = timezone.utc
NY = ZoneInfo("America/New_York")
KOLKATA = ZoneInfo("Asia/Kolkata")
SYDNEY = ZoneInfo("Australia/Sydney")
UTC_Z = ZoneInfo("UTC")


def _utc(y, m, d, hh=0, mm=0, ss=0):
    return datetime(y, m, d, hh, mm, ss, tzinfo=UTC)


def _local(tz, y, m, d, hh=0, mm=0, ss=0):
    return datetime(y, m, d, hh, mm, ss, tzinfo=tz)


def _in_business_hours(instant: datetime, tz) -> bool:
    local = instant.astimezone(tz)
    if local.weekday() not in BUSINESS_DAYS:
        return False
    return any(start <= local.time() < end for start, end in BUSINESS_WINDOWS)


# ---------------------------------------------------------------- snapping ---

def test_inside_morning_window_unchanged():
    t = _local(UTC_Z, 2026, 7, 14, 10, 30)  # Tuesday
    assert snap_to_business_hours(t, UTC_Z) == t


def test_inside_afternoon_window_unchanged():
    t = _local(UTC_Z, 2026, 7, 14, 14, 45)
    assert snap_to_business_hours(t, UTC_Z) == t


def test_exact_window_open_0900_unchanged():
    t = _local(UTC_Z, 2026, 7, 14, 9, 0)
    assert snap_to_business_hours(t, UTC_Z) == t


def test_115959_unchanged_and_120000_snaps_to_1300():
    ok = _local(UTC_Z, 2026, 7, 14, 11, 59, 59)
    assert snap_to_business_hours(ok, UTC_Z) == ok
    noon = _local(UTC_Z, 2026, 7, 14, 12, 0)
    assert snap_to_business_hours(noon, UTC_Z) == _local(UTC_Z, 2026, 7, 14, 13, 0)


def test_lunch_1230_snaps_to_1300():
    t = _local(UTC_Z, 2026, 7, 14, 12, 30)
    assert snap_to_business_hours(t, UTC_Z) == _local(UTC_Z, 2026, 7, 14, 13, 0)


def test_before_open_snaps_to_0900():
    t = _local(UTC_Z, 2026, 7, 14, 6, 12)
    assert snap_to_business_hours(t, UTC_Z) == _local(UTC_Z, 2026, 7, 14, 9, 0)


def test_155959_unchanged_and_160000_rolls_to_next_day():
    ok = _local(UTC_Z, 2026, 7, 14, 15, 59, 59)
    assert snap_to_business_hours(ok, UTC_Z) == ok
    t = _local(UTC_Z, 2026, 7, 14, 16, 0)
    assert snap_to_business_hours(t, UTC_Z) == _local(UTC_Z, 2026, 7, 15, 9, 0)


def test_friday_evening_rolls_to_monday():
    t = _local(UTC_Z, 2026, 7, 17, 18, 5)  # Friday 6pm
    assert snap_to_business_hours(t, UTC_Z) == _local(UTC_Z, 2026, 7, 20, 9, 0)  # Monday


def test_saturday_and_sunday_roll_to_monday():
    for day in (18, 19):  # Sat, Sun
        t = _local(UTC_Z, 2026, 7, day, 10, 0)
        assert snap_to_business_hours(t, UTC_Z) == _local(UTC_Z, 2026, 7, 20, 9, 0)


def test_midnight_monday_snaps_to_9am_same_day():
    t = _local(UTC_Z, 2026, 7, 20, 0, 0)
    assert snap_to_business_hours(t, UTC_Z) == _local(UTC_Z, 2026, 7, 20, 9, 0)


def test_timezone_awareness_kolkata():
    # 05:00 UTC on a Tuesday = 10:30 IST -> inside business hours for an Indian lead.
    t = _utc(2026, 7, 14, 5, 0)
    assert snap_to_business_hours(t, KOLKATA) == t
    # ...but for a New York lead that's 1:00 AM -> snaps to 9:00 AM EDT (13:00 UTC).
    assert snap_to_business_hours(t, NY) == _local(NY, 2026, 7, 14, 9, 0)


def test_dst_spring_forward_new_york():
    # US DST starts Sun 2026-03-08. Saturday snaps to Monday 09:00 EDT (UTC-4).
    t = _local(NY, 2026, 3, 7, 10, 0)  # Saturday
    snapped = snap_to_business_hours(t, NY)
    assert snapped == _local(NY, 2026, 3, 9, 9, 0)
    assert snapped.astimezone(UTC) == _utc(2026, 3, 9, 13, 0)  # EDT, not EST


def test_dst_fall_back_sydney():
    # Sydney DST ends Sun 2026-04-05. Sunday snaps to Monday 09:00 AEST (UTC+10).
    t = _local(SYDNEY, 2026, 4, 5, 12, 0)
    snapped = snap_to_business_hours(t, SYDNEY)
    assert snapped == _local(SYDNEY, 2026, 4, 6, 9, 0)
    assert snapped.astimezone(UTC) == _utc(2026, 4, 5, 23, 0)


def test_naive_datetime_rejected():
    try:
        snap_to_business_hours(datetime(2026, 7, 14, 10, 0), UTC_Z)
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_result_always_utc_and_in_hours_fuzz():
    rng = random.Random(42)
    for _ in range(500):
        t = _utc(2026, 1, 1) + timedelta(minutes=rng.randrange(0, 60 * 24 * 365))
        tz = rng.choice([UTC_Z, NY, KOLKATA, SYDNEY])
        s = snap_to_business_hours(t, tz)
        assert s.tzinfo is not None
        assert s >= t, f"snap went backwards: {t} -> {s} ({tz})"
        assert _in_business_hours(s, tz), f"snap not in business hours: {t} -> {s} ({tz})"


# ------------------------------------------------------------ slot walking ---

def test_empty_occupied_returns_candidate():
    c = _utc(2026, 7, 14, 10, 0)
    assert find_free_slot(c, [], SCHEDULE_GAP) == c


def test_exact_conflict_pushes_by_gap():
    c = _utc(2026, 7, 14, 10, 0)
    assert find_free_slot(c, [c], SCHEDULE_GAP) == c + SCHEDULE_GAP


def test_conflict_exactly_gap_away_is_allowed():
    c = _utc(2026, 7, 14, 10, 0)
    assert find_free_slot(c, [c + SCHEDULE_GAP], SCHEDULE_GAP) == c
    assert find_free_slot(c, [c - SCHEDULE_GAP], SCHEDULE_GAP) == c


def test_conflict_one_second_inside_gap_pushes():
    c = _utc(2026, 7, 14, 10, 0)
    near = c + SCHEDULE_GAP - timedelta(seconds=1)
    assert find_free_slot(c, [near], SCHEDULE_GAP) == near + SCHEDULE_GAP


def test_recent_past_dispatch_blocks():
    c = _utc(2026, 7, 14, 10, 0)
    just_sent = c - timedelta(seconds=45)
    assert find_free_slot(c, [just_sent], SCHEDULE_GAP) == just_sent + SCHEDULE_GAP


def test_old_past_dispatch_ignored():
    c = _utc(2026, 7, 14, 10, 0)
    assert find_free_slot(c, [c - timedelta(minutes=10)], SCHEDULE_GAP) == c


def test_cascading_conflicts():
    c = _utc(2026, 7, 14, 10, 0)
    occupied = [c, c + timedelta(minutes=2), c + timedelta(minutes=4)]
    assert find_free_slot(c, occupied, SCHEDULE_GAP) == c + timedelta(minutes=6)


def test_unsorted_occupied_input():
    c = _utc(2026, 7, 14, 10, 0)
    occupied = [c + timedelta(minutes=4), c, c + timedelta(minutes=2)]
    assert find_free_slot(c, occupied, SCHEDULE_GAP) == c + timedelta(minutes=6)


def test_send_gap_30s_defers_only_30s():
    now = _utc(2026, 7, 14, 22, 0)  # 10 PM — send ignores business hours
    just_sent = now - timedelta(seconds=10)
    assert find_free_slot(now, [just_sent], SEND_GAP) == just_sent + SEND_GAP  # +20s from now


def test_send_no_conflict_is_instant_even_at_night_and_weekend():
    now = _utc(2026, 7, 18, 3, 0)  # Saturday 3 AM
    assert find_free_slot(now, [], SEND_GAP) == now


def test_bump_spills_over_lunch_break():
    tz = UTC_Z
    snap = lambda x: snap_to_business_hours(x, tz)
    c = _local(tz, 2026, 7, 14, 11, 59)
    occupied = [c]  # conflict pushes to 12:01 -> snaps to 13:00
    assert find_free_slot(c, occupied, SCHEDULE_GAP, snap) == _local(tz, 2026, 7, 14, 13, 0)


def test_bump_spills_over_friday_close_to_monday():
    tz = UTC_Z
    snap = lambda x: snap_to_business_hours(x, tz)
    c = _local(tz, 2026, 7, 17, 15, 59)  # Friday
    assert find_free_slot(c, [c], SCHEDULE_GAP, snap) == _local(tz, 2026, 7, 20, 9, 0)


def test_burst_of_200_schedules_all_valid():
    """Simulate 200 consecutive Schedule clicks: every slot must be in business hours,
    >= 2 min from every other, and monotonically increasing."""
    tz = NY
    snap = lambda x: snap_to_business_hours(x, tz)
    start = _local(NY, 2026, 7, 17, 15, 30).astimezone(UTC)  # Friday near close -> spills to Monday+
    occupied: list[datetime] = []
    for i in range(200):
        slot = find_free_slot(start, occupied, SCHEDULE_GAP, snap)
        assert _in_business_hours(slot, tz), f"slot {i} outside business hours: {slot.astimezone(tz)}"
        for other in occupied:
            assert abs(slot - other) >= SCHEDULE_GAP, f"slot {i} too close to {other}"
        occupied.append(slot)
    assert sorted(occupied) == occupied
    # 90 slots/day (3h+3h at 2-min spacing) -> 200 slots must span >= 3 business days.
    days = {s.astimezone(tz).date() for s in occupied}
    assert len(days) >= 3
    assert all(d.weekday() in BUSINESS_DAYS for d in days)


def test_mixed_send_and_schedule_invariants_fuzz():
    """Random interleaving of manual sends (30s gap, no hours) and schedules (2min gap,
    business hours): the invariants the product promises must hold for every booking."""
    rng = random.Random(7)
    tz = KOLKATA
    snap = lambda x: snap_to_business_hours(x, tz)
    now = _utc(2026, 7, 14, 5, 30)  # Tuesday 11:00 IST
    occupied: list[datetime] = []
    for i in range(150):
        now += timedelta(seconds=rng.randrange(0, 90))  # clock advances between clicks
        if rng.random() < 0.4:  # manual send
            slot = find_free_slot(now, occupied, SEND_GAP)
            assert slot >= now
            assert slot - now <= timedelta(seconds=35) or any(
                abs(slot - o) < timedelta(minutes=5) for o in occupied
            ), f"send {i} deferred too far: {slot - now}"
            for other in occupied:
                assert abs(slot - other) >= SEND_GAP, f"send {i} violates 30s gap"
        else:  # schedule button
            slot = find_free_slot(now + timedelta(minutes=1), occupied, SCHEDULE_GAP, snap)
            assert _in_business_hours(slot, tz), f"schedule {i} outside hours: {slot.astimezone(tz)}"
            for other in occupied:
                assert abs(slot - other) >= SCHEDULE_GAP, f"schedule {i} violates 2min gap"
        occupied.append(slot)


def test_schedule_now_plus_one_minute_inside_hours_stays():
    tz = UTC_Z
    snap = lambda x: snap_to_business_hours(x, tz)
    now = _local(tz, 2026, 7, 14, 10, 0)
    slot = find_free_slot(now + timedelta(minutes=1), [], SCHEDULE_GAP, snap)
    assert slot == now + timedelta(minutes=1)


def test_year_boundary_and_leap_handling():
    # Fri 2027-12-31? Use 2027-12-31 (Friday) 17:00 -> Mon 2028-01-03 09:00.
    t = _local(UTC_Z, 2027, 12, 31, 17, 0)
    assert t.weekday() == 4
    assert snap_to_business_hours(t, UTC_Z) == _local(UTC_Z, 2028, 1, 3, 9, 0)
    # Leap day 2028-02-29 is a Tuesday -> valid business day.
    leap = _local(UTC_Z, 2028, 2, 29, 10, 0)
    assert snap_to_business_hours(leap, UTC_Z) == leap


def _run_all():
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"  PASS  {name}")
        except AssertionError as exc:
            failed += 1
            print(f"  FAIL  {name}: {exc}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"  ERROR {name}: {type(exc).__name__}: {exc}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return failed


if __name__ == "__main__":
    sys.exit(1 if _run_all() else 0)
