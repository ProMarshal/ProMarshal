from datetime import datetime, timezone

from app.action_items.temporal_parser import parse_temporal_hints


def test_parse_temporal_hints_supports_plain_weekday_tokens():
    # Sunday in Asia/Kolkata.
    anchor = datetime(2026, 3, 15, 12, 0, tzinfo=timezone.utc)
    result = parse_temporal_hints(
        text="send invite for tuesday at 10am",
        timezone_name="Asia/Kolkata",
        now_utc=anchor,
    )
    assert result.target_datetime is not None
    local = result.target_datetime.astimezone(timezone.utc)
    # 2026-03-17 is the upcoming Tuesday from 2026-03-15.
    assert local.year == 2026
    assert local.month == 3
    assert local.day == 17
