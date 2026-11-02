import json
from datetime import datetime

import pytest

from app.extensions import db
from app.models import BandwidthRule, Job
from app.services import bandwidth as bw

WEEKDAYS = [0, 1, 2, 3, 4]
EVERY_DAY = list(range(7))


def make_rule(days=EVERY_DAY, start=0, end=0, value=1, unit="MiB", *,
              scope="everything", remotes=(), job_id=None, enabled=True):
    return BandwidthRule(
        job_id=job_id,
        **bw._rule_values(days, start, end, value, unit, enabled, scope, remotes),
    )


def make_job(source="/mnt/a", destination="gdrive:b", id=1):
    return Job(id=id, name="j", source=source, destination=destination,
               sync_type="copy")


def at(day, hour, minute=0):
    """A local time on the given weekday (Monday = 0) in a known week."""
    return datetime(2026, 11, 2 + day, hour, minute)  # 2026-11-02 is a Monday


def clock(text):
    hour, minute = text.split(":")
    return int(hour) * 60 + int(minute)


# --- When a window is open ---


def test_window_within_a_day():
    days, start, end = WEEKDAYS, clock("09:00"), clock("17:00")
    assert bw.window_active(days, start, end, at(0, 9))
    assert bw.window_active(days, start, end, at(4, 16, 59))
    assert not bw.window_active(days, start, end, at(0, 17))  # end is exclusive
    assert not bw.window_active(days, start, end, at(0, 8, 59))
    assert not bw.window_active(days, start, end, at(5, 12))  # Saturday


def test_all_day_window():
    assert bw.window_active([2], 0, 0, at(2, 0))
    assert bw.window_active([2], 0, 0, at(2, 23, 59))
    assert not bw.window_active([2], 0, 0, at(3, 0))


def test_overnight_window_belongs_to_the_day_it_starts():
    days, start, end = [4], clock("22:00"), clock("06:00")  # Friday night
    assert bw.window_active(days, start, end, at(4, 23))
    assert bw.window_active(days, start, end, at(5, 5, 59))  # Saturday morning
    assert not bw.window_active(days, start, end, at(5, 6))
    assert not bw.window_active(days, start, end, at(4, 5))  # Friday morning
    assert not bw.window_active(days, start, end, at(5, 23))


def test_overnight_window_wraps_from_sunday_to_monday():
    assert bw.window_active([6], clock("23:00"), clock("01:00"), at(0, 0, 30))


def test_disabled_rule_is_never_active():
    assert not bw.rule_active(make_rule(enabled=False), at(0, 12))


# --- Which remotes a job touches ---


@pytest.mark.parametrize(
    "fs, windows, expected",
    [
        ("/mnt/data", False, None),
        ("relative/dir", False, None),
        ("C:\\Data", True, None),
        ("C:/Data", True, None),
        ("\\\\server\\share", True, None),
        ("gdrive:Backups", True, "gdrive"),
        ("gdrive:", False, "gdrive"),
        ("my remote,env_auth=true:path", False, "my remote"),
        (":s3,env_auth:bucket", False, ""),
        ("c:Backups", False, "c"),  # a single-letter remote off Windows
    ],
)
def test_fs_remote(fs, windows, expected):
    assert bw.fs_remote(fs, windows) == expected


def test_job_remotes():
    assert bw.job_remotes(make_job("/a", "/b")) == (set(), False)
    assert bw.job_remotes(make_job("/a", "gdrive:x")) == ({"gdrive"}, True)
    assert bw.job_remotes(make_job("pcloud:a", "gdrive:x")) == (
        {"pcloud", "gdrive"}, True,
    )
    assert bw.job_remotes(make_job("/a", ":s3,env_auth:b")) == (set(), True)


# --- Which limit wins ---


def limit(rules, jobs, when=None):
    found = bw.effective_limit(rules, jobs, when or at(2, 12))
    return found.bytes_per_second if found else None


def test_no_rules_means_no_limit():
    assert limit([], [make_job()]) is None


def test_limit_for_everything_applies_with_nothing_running():
    assert limit([make_rule(value=2)], []) == 2 * 1024**2


def test_units():
    for unit, factor in [("KiB", 1024), ("MiB", 1024**2), ("GiB", 1024**3),
                         ("Mbit", 125_000)]:
        assert limit([make_rule(value=3, unit=unit)], []) == 3 * factor


def test_lowest_limit_wins():
    rules = [make_rule(value=5), make_rule(value=2), make_rule(value=9)]
    assert limit(rules, [make_job()]) == 2 * 1024**2


def test_only_open_windows_count():
    rules = [make_rule(WEEKDAYS, clock("09:00"), clock("17:00"), value=1)]
    assert limit(rules, [], at(1, 12)) == 1024**2
    assert limit(rules, [], at(1, 20)) is None


def test_all_remotes_applies_to_jobs_using_any_remote_only():
    rules = [make_rule(value=1, scope="all_remotes")]
    assert limit(rules, []) is None
    assert limit(rules, [make_job("/a", "/b")]) is None
    assert limit(rules, [make_job("/a", "gdrive:x")]) == 1024**2


def test_all_remotes_covers_a_remote_added_later():
    rules = [make_rule(value=1, scope="all_remotes")]
    brand_new = make_job("/a", "added-next-year:x")
    assert limit(rules, [brand_new]) == 1024**2


def test_specific_remotes_match_by_name():
    rules = [make_rule(value=1, scope="remotes", remotes=["gdrive"])]
    assert limit(rules, [make_job("/a", "gdrive:x")]) == 1024**2
    assert limit(rules, [make_job("gdrive:x", "/a")]) == 1024**2
    assert limit(rules, [make_job("/a", "pcloud:x")]) is None
    assert limit(rules, [make_job("/a", "/b")]) is None


def test_job_rules_apply_to_that_job_only():
    rules = [make_rule(value=1, job_id=7)]
    assert limit(rules, [make_job(id=7)]) == 1024**2
    assert limit(rules, [make_job(id=8)]) is None
    assert limit(rules, []) is None


def test_job_and_global_rules_combine_to_the_lowest():
    rules = [
        make_rule(value=8, scope="all_remotes"),
        make_rule(value=3, job_id=7),
    ]
    assert limit(rules, [make_job(id=7)]) == 3 * 1024**2
    assert limit(rules, [make_job(id=8)]) == 8 * 1024**2


def test_concurrent_jobs_share_the_lowest_limit():
    rules = [
        make_rule(value=1, scope="remotes", remotes=["slow"]),
        make_rule(value=50, scope="remotes", remotes=["fast"]),
    ]
    jobs = [make_job("/a", "slow:x", id=1), make_job("/a", "fast:x", id=2)]
    assert limit(rules, jobs) == 1024**2


# --- Reading what the editor sends ---


def valid(**overrides):
    return {"days": [0, 1], "all_day": False, "start": "09:00", "end": "17:00",
            "value": 5, "unit": "MiB", **overrides}


def parse(items, scoped=False):
    return bw.parse_rules(json.dumps(items), allow_remote_scope=scoped)


def test_parse_valid_rule():
    [rule] = parse([valid(value=2.5, unit="Mbit")])
    assert rule["days"] == "0,1"
    assert (rule["start_minute"], rule["end_minute"]) == (540, 1020)
    assert (rule["limit_value"], rule["limit_unit"]) == (2.5, "Mbit")
    assert rule["scope"] == "everything"


def test_parse_all_day_ignores_times():
    [rule] = parse([valid(all_day=True, start="", end="")])
    assert (rule["start_minute"], rule["end_minute"]) == (0, 0)


def test_parse_keeps_scope_only_when_allowed():
    item = valid(scope="remotes", remotes=["a", " b ", "a", ""])
    [kept] = parse([item], scoped=True)
    assert (kept["scope"], json.loads(kept["remotes"])) == ("remotes", ["a", "b"])
    [dropped] = parse([item], scoped=False)
    assert (dropped["scope"], dropped["remotes"]) == ("everything", "[]")


@pytest.mark.parametrize(
    "overrides, message",
    [
        ({"days": []}, "at least one day"),
        ({"days": [7]}, "at least one day"),
        ({"days": ["x"]}, "at least one day"),
        ({"start": "25:00"}, "HH:MM"),
        ({"end": ""}, "HH:MM"),
        ({"start": "09:00", "end": "09:00"}, "same"),
        ({"value": 0}, "above zero"),
        ({"value": -3}, "above zero"),
        ({"value": "fast"}, "above zero"),
        ({"value": None}, "above zero"),
        ({"value": float("nan")}, "above zero"),
        ({"unit": "furlongs"}, "above zero"),
        ({"value": 0.0001, "unit": "KiB"}, "below 1 KiB/s"),
        ({"scope": "remotes", "remotes": []}, "at least one remote"),
        ({"scope": "sometimes"}, "applies to"),
    ],
)
def test_parse_rejects_bad_rules(overrides, message):
    with pytest.raises(ValueError, match=message):
        parse([valid(**overrides)], scoped=True)


@pytest.mark.parametrize("raw", ["not json", "{}", "[1]", "null"])
def test_parse_rejects_malformed_payload(raw):
    with pytest.raises(ValueError, match="could not be read"):
        bw.parse_rules(raw, allow_remote_scope=False)


def test_parse_rejects_missing_payload():
    with pytest.raises(ValueError):
        bw.parse_rules(None, allow_remote_scope=False)


def test_parse_caps_rule_count():
    with pytest.raises(ValueError, match="at most"):
        parse([valid()] * (bw.MAX_RULES + 1))


def test_error_names_the_rule():
    with pytest.raises(ValueError, match="rule 2"):
        parse([valid(), valid(days=[])])


# --- Wording ---


def test_describe_rule():
    rule = make_rule(WEEKDAYS, clock("09:00"), clock("17:30"), 2.5, "MiB")
    assert bw.describe_rule(rule) == "Weekdays · 09:00–17:30 · 2.5 MiB/s"
    assert bw.describe_rule(make_rule([5, 6], 0, 0, 10, "Mbit")) == (
        "Weekends · all day · 10 Mbit/s"
    )


@pytest.mark.parametrize(
    "days, text",
    [
        (EVERY_DAY, "Every day"),
        ([0, 1, 2], "Mon–Wed"),
        ([0, 2, 4], "Mon, Wed, Fri"),
        ([0, 1, 4], "Mon, Tue, Fri"),
        ([6], "Sun"),
    ],
)
def test_format_days(days, text):
    assert bw.format_days(days) == text


def test_rule_round_trips_through_the_editor_format():
    rule = make_rule([1, 3], clock("22:00"), clock("06:00"), 4, "KiB",
                     scope="remotes", remotes=["a"])
    [parsed] = parse([bw.rule_to_dict(rule)], scoped=True)
    assert parsed == bw._rule_values(
        [1, 3], clock("22:00"), clock("06:00"), 4, "KiB",
        scope="remotes", remotes=["a"],
    )


# --- Old free-text limits ---


def test_legacy_plain_rate():
    [rule] = bw.legacy_rules("10M")
    assert (rule["limit_value"], rule["limit_unit"]) == (10, "MiB")
    assert (rule["days"], rule["start_minute"], rule["end_minute"]) == (
        "0,1,2,3,4,5,6", 0, 0,
    )


@pytest.mark.parametrize(
    "text, expected",
    [("512", (512, "KiB")), ("512k", (512, "KiB")), ("2G", (2, "GiB")),
     ("2048b", (2, "KiB"))],
)
def test_legacy_rate_units(text, expected):
    [rule] = bw.legacy_rules(text)
    assert (rule["limit_value"], rule["limit_unit"]) == expected


def test_legacy_empty_and_off_mean_no_rules():
    assert bw.legacy_rules("") == []
    assert bw.legacy_rules(None) == []
    assert bw.legacy_rules("off") == []


def test_legacy_daily_timetable():
    [rule] = bw.legacy_rules("08:00,5M 23:00,off")
    assert rule["days"] == "0,1,2,3,4,5,6"
    assert (rule["start_minute"], rule["end_minute"]) == (clock("08:00"), clock("23:00"))
    assert (rule["limit_value"], rule["limit_unit"]) == (5, "MiB")


def test_legacy_timetable_wraps_past_midnight():
    # 5M from 23:00 until the next entry at 06:00 the following day.
    [rule] = bw.legacy_rules("06:00,off 23:00,5M")
    assert (rule["start_minute"], rule["end_minute"]) == (clock("23:00"), clock("06:00"))
    assert rule["days"] == "0,1,2,3,4,5,6"


def test_legacy_weekday_timetable():
    # Friday 23:00 until Monday 06:00 is split into one rule per kind of day.
    rules = bw.legacy_rules("Fri-23:00,10M Mon-06:00,off")
    found = {(r["days"], r["start_minute"], r["end_minute"]) for r in rules}
    assert found == {("4", clock("23:00"), 0), ("5,6", 0, 0), ("0", 0, clock("06:00"))}
    assert {r["limit_value"] for r in rules} == {10}


def test_legacy_numeric_days_count_from_sunday():
    [rule] = bw.legacy_rules("0-08:00,1M 0-20:00,off")  # Sunday
    assert rule["days"] == "6"


@pytest.mark.parametrize("text", ["fast", "10M:20M", "25:00,5M 26:00,off",
                                  "Fun-08:00,5M 09:00,off", "08:00,0"])
def test_legacy_unreadable_values(text):
    assert bw.legacy_rules(text) is None


def test_migration_converts_and_clears(app):
    with app.app_context():
        db.session.add_all([
            Job(name="good", source="/a", destination="r:b", sync_type="copy",
                bwlimit="10M"),
            Job(name="odd", source="/a", destination="r:b", sync_type="copy",
                bwlimit="10M:20M"),
            Job(name="none", source="/a", destination="r:b", sync_type="copy"),
        ])
        db.session.commit()
        bw.migrate_legacy_limits()
        jobs = {j.name: j for j in Job.query.all()}
        assert [r.limit_value for r in jobs["good"].bandwidth_rules] == [10]
        assert jobs["good"].bwlimit == ""
        assert jobs["odd"].bandwidth_rules == []
        assert jobs["odd"].bwlimit == "10M:20M"  # kept: can't be converted
        assert jobs["none"].bandwidth_rules == []

        bw.migrate_legacy_limits()  # idempotent
        assert BandwidthRule.query.count() == 1


# --- Setting rclone's limiter ---


def add_global_rule(app, **kwargs):
    with app.app_context():
        db.session.add(make_rule(**kwargs))
        db.session.commit()


def test_controller_leaves_rclone_alone_without_rules(app, fake_rclone):
    with app.app_context():
        app.config["BANDWIDTH"].reconcile()
    assert fake_rclone.calls == []


def test_controller_sets_then_clears_the_limit(app, fake_rclone):
    add_global_rule(app, value=2)
    controller = app.config["BANDWIDTH"]
    with app.app_context():
        controller.reconcile()
        assert fake_rclone.bytes_per_second == 2 * 1024**2
        assert fake_rclone.calls == [("bwlimit", f"{2 * 1024**2}b")]

        controller.reconcile()  # unchanged: no second call
        assert len(fake_rclone.calls) == 1

        BandwidthRule.query.delete()
        db.session.commit()
        controller.reconcile()
        assert fake_rclone.bytes_per_second == -1
        assert fake_rclone.calls[-1] == ("bwlimit", "off")


def test_controller_repairs_a_limit_rclone_forgot(app, fake_rclone):
    add_global_rule(app, value=2)
    controller = app.config["BANDWIDTH"]
    with app.app_context():
        controller.reconcile()
        fake_rclone.bytes_per_second = -1  # rclone restarted
        controller.reconcile()
        assert fake_rclone.bytes_per_second == 2 * 1024**2


def test_controller_survives_rclone_being_down(app, fake_rclone, monkeypatch):
    add_global_rule(app, value=2)

    def down():
        raise ConnectionError("rclone is not running")

    monkeypatch.setattr(fake_rclone, "get_bwlimit", down)
    with app.app_context():
        app.config["BANDWIDTH"].reconcile()  # must not raise


def test_limit_is_in_place_before_the_job_starts(app, fake_rclone):
    add_global_rule(app, value=1, scope="remotes", remotes=["remote"])
    with app.app_context():
        job = make_job("/mnt/photos", "remote:photos", id=None)
        job.name = "Photos"
        db.session.add(job)
        db.session.commit()
        app.config["JOB_RUNNER"].start_job(job)
    kinds = [call[0] for call in fake_rclone.calls]
    assert kinds == ["bwlimit", "copy"]


def test_limit_is_lifted_when_the_run_is_stopped(app, fake_rclone):
    add_global_rule(app, value=1, scope="remotes", remotes=["remote"])
    runner = app.config["JOB_RUNNER"]
    with app.app_context():
        job = make_job("/mnt/photos", "remote:photos", id=None)
        db.session.add(job)
        db.session.commit()
        run = runner.start_job(job)
        assert fake_rclone.bytes_per_second == 1024**2
        runner.stop_job(run)
        assert fake_rclone.bytes_per_second == -1


def test_unrelated_job_is_not_limited(app, fake_rclone):
    add_global_rule(app, value=1, scope="remotes", remotes=["other"])
    with app.app_context():
        job = make_job("/mnt/photos", "remote:photos", id=None)
        db.session.add(job)
        db.session.commit()
        app.config["JOB_RUNNER"].start_job(job)
    assert fake_rclone.bytes_per_second == -1


# --- Saving rules from the pages ---

JOB_FORM = {
    "name": "Photos",
    "source": "/mnt/photos",
    "destination": "remote:photos",
    "sync_type": "copy",
}


def post_rules(client, url, items, **form):
    return client.post(
        url, data={**form, "bandwidth_rules": json.dumps(items)},
        follow_redirects=True,
    )


def test_settings_page_shows_saved_rules(app, client, fake_rclone):
    add_global_rule(app, value=3, scope="all_remotes")
    page = client.get("/settings/").data.decode()
    assert "bw-editor" in page
    assert "all_remotes" in page


def test_save_global_rules(app, client, fake_rclone):
    items = [
        valid(scope="all_remotes"),
        valid(scope="remotes", remotes=["gdrive"], days=[5, 6]),
    ]
    response = post_rules(client, "/settings/bandwidth", items)
    assert b"Bandwidth limits saved (2 rules)" in response.data
    with app.app_context():
        rules = BandwidthRule.query.filter_by(job_id=None).all()
        assert [r.scope for r in rules] == ["all_remotes", "remotes"]
        assert rules[1].remote_list() == ["gdrive"]


def test_saving_global_rules_replaces_the_old_ones(app, client, fake_rclone):
    add_global_rule(app, value=9)
    post_rules(client, "/settings/bandwidth", [valid(value=4)])
    with app.app_context():
        assert [r.limit_value for r in BandwidthRule.query.all()] == [4]


def test_saving_no_rules_removes_the_limit(app, client, fake_rclone):
    post_rules(client, "/settings/bandwidth", [valid(all_day=True, days=EVERY_DAY)])
    assert fake_rclone.bytes_per_second == 5 * 1024**2
    post_rules(client, "/settings/bandwidth", [])
    assert fake_rclone.bytes_per_second == -1


def test_invalid_global_rules_change_nothing(app, client, fake_rclone):
    add_global_rule(app, value=9)
    response = post_rules(client, "/settings/bandwidth", [valid(days=[])])
    assert b"pick at least one day" in response.data
    with app.app_context():
        assert [r.limit_value for r in BandwidthRule.query.all()] == [9]


def test_global_rules_do_not_touch_job_rules(app, client, fake_rclone):
    client.post("/jobs/create", data=JOB_FORM)
    with app.app_context():
        job_id = Job.query.one().id
    post_rules(client, f"/jobs/{job_id}/edit", [valid()], **JOB_FORM)
    post_rules(client, "/settings/bandwidth", [])
    with app.app_context():
        assert BandwidthRule.query.filter_by(job_id=job_id).count() == 1


def test_create_job_with_rules(app, client):
    response = post_rules(client, "/jobs/create", [valid()], **JOB_FORM)
    assert response.status_code == 200
    with app.app_context():
        [rule] = Job.query.one().bandwidth_rules
        assert (rule.limit_value, rule.scope) == (5, "everything")


def test_job_rules_ignore_scope(app, client):
    post_rules(client, "/jobs/create",
               [valid(scope="remotes", remotes=["x"])], **JOB_FORM)
    with app.app_context():
        [rule] = Job.query.one().bandwidth_rules
        assert (rule.scope, rule.remote_list()) == ("everything", [])


def test_edit_job_replaces_rules(app, client):
    post_rules(client, "/jobs/create", [valid(value=5)], **JOB_FORM)
    with app.app_context():
        job_id = Job.query.one().id
    post_rules(client, f"/jobs/{job_id}/edit", [valid(value=1), valid(value=2)],
               **JOB_FORM)
    with app.app_context():
        assert [r.limit_value for r in Job.query.one().bandwidth_rules] == [1, 2]
    post_rules(client, f"/jobs/{job_id}/edit", [], **JOB_FORM)
    with app.app_context():
        assert Job.query.one().bandwidth_rules == []


def test_edit_without_rules_field_keeps_them(app, client):
    post_rules(client, "/jobs/create", [valid()], **JOB_FORM)
    with app.app_context():
        job_id = Job.query.one().id
    client.post(f"/jobs/{job_id}/edit", data={**JOB_FORM, "name": "Renamed"})
    with app.app_context():
        assert len(Job.query.one().bandwidth_rules) == 1


def test_invalid_job_rules_create_nothing(app, client):
    response = post_rules(client, "/jobs/create", [valid(value=0)], **JOB_FORM)
    assert b"above zero" in response.data
    with app.app_context():
        assert Job.query.count() == 0


def test_job_form_shows_its_rules(app, client, fake_rclone):
    post_rules(client, "/jobs/create", [valid(value=7)], **JOB_FORM)
    with app.app_context():
        job_id = Job.query.one().id
    page = client.get(f"/jobs/{job_id}/edit").data.decode()
    assert "bw-editor" in page
    assert '"value": 7' in page


def test_deleting_a_job_deletes_its_rules(app, client):
    post_rules(client, "/jobs/create", [valid()], **JOB_FORM)
    with app.app_context():
        job_id = Job.query.one().id
    client.post(f"/jobs/{job_id}/delete")
    with app.app_context():
        assert BandwidthRule.query.count() == 0
