from datetime import datetime

import pytz

from app.services.cron_trigger import crontab_for_apscheduler, cron_trigger


def _next_day(expr, after=datetime(2026, 11, 2, 12, 0)):  # a Monday
    trigger = cron_trigger(expr, timezone=pytz.utc)
    return trigger.get_next_fire_time(None, pytz.utc.localize(after)).strftime("%a")


def test_numeric_weekdays_count_from_sunday():
    assert _next_day("0 3 * * 0") == "Sun"
    assert _next_day("0 3 * * 7") == "Sun"
    assert _next_day("0 3 * * 1") == "Mon"
    assert _next_day("0 3 * * 6") == "Sat"


def test_ranges_lists_and_steps_are_rewritten_as_names():
    assert crontab_for_apscheduler("0 3 * * 1-5") == "0 3 * * mon,tue,wed,thu,fri"
    assert crontab_for_apscheduler("0 3 * * 0,6") == "0 3 * * sun,sat"
    assert crontab_for_apscheduler("0 3 * * 5-7") == "0 3 * * sun,fri,sat"
    assert crontab_for_apscheduler("0 3 * * */2") == "0 3 * * sun,tue,thu,sat"
    assert crontab_for_apscheduler("0 3 * * sat-mon") == "0 3 * * sun,mon,sat"
    assert crontab_for_apscheduler("0 3 * * mon-fri") == "0 3 * * mon,tue,wed,thu,fri"


def test_untouched_when_weekday_is_any_or_unreadable():
    assert crontab_for_apscheduler("*/15 * * * *") == "*/15 * * * *"
    assert crontab_for_apscheduler("0 3 * * 9") == "0 3 * * 9"
    assert crontab_for_apscheduler("not a cron") == "not a cron"
