import json

import pytest

from app.extensions import db
from app.models import Job, Schedule
from app.services import extra_flags

# A slice of rclone's options/info "main" list, with the types that matter.
MAIN_OPTIONS = [
    {"Name": n, "FieldName": f, "Type": t}
    for n, f, t in [
        ("max_backlog", "MaxBacklog", "int"),
        ("buffer_size", "BufferSize", "SizeSuffix"),
        ("timeout", "Timeout", "Duration"),
        ("ignore_size", "IgnoreSize", "bool"),
        ("tpslimit", "TPSLimit", "float64"),
        ("user_agent", "UserAgent", "string"),
        ("log_level", "LogLevel", "LogLevel"),
        ("update", "UpdateOlder", "bool"),
        ("transfers", "Transfers", "int"),
        ("checksum", "CheckSum", "bool"),
        ("dry_run", "DryRun", "bool"),
    ]
]
OPTIONS = {o["Name"]: o for o in MAIN_OPTIONS}

JOB_FORM = {
    "name": "Photos",
    "source": "/mnt/photos",
    "destination": "nas:photos",
    "sync_type": "copy",
}


def parse(text):
    return extra_flags.parse(text, OPTIONS)


# --- Parsing ---

def test_parses_flags_into_typed_config_keys():
    config, errors = parse(
        "--max-backlog 200000\n--buffer-size=32M\n--ignore-size\n"
        "--tpslimit 2.5\n--timeout 2m\n--user-agent my agent\n"
    )
    assert errors == []
    assert config == {
        "MaxBacklog": 200000,
        "BufferSize": "32M",
        "IgnoreSize": True,
        "TPSLimit": 2.5,
        "Timeout": "2m",
        "UserAgent": "my agent",
    }


def test_flag_name_differs_from_config_key():
    config, _ = parse("--update")
    assert config == {"UpdateOlder": True}


def test_bool_can_be_turned_off_and_blank_lines_are_ignored():
    config, errors = parse("\n# note\n--ignore-size=false\n\n")
    assert errors == [] and config == {"IgnoreSize": False}


@pytest.mark.parametrize("line, fragment", [
    ("--max-bakclog 5", "--max-bakclog is not an rclone option"),
    ("--transfers 8", "--transfers is set by the Transfers field"),
    ("--checksum", "--checksum is set by the File comparison method"),
    ("--exclude *.tmp", "--exclude is a filter flag"),
    ("--dry-run", "--dry-run does not make sense"),
    ("--log-level DEBUG", "--log-level takes a value this form can't carry"),
    ("--max-backlog many", "--max-backlog needs a whole number"),
    ("--buffer-size lots", "--buffer-size needs a size"),
    ("--timeout soon", "--timeout needs a duration"),
    ("--max-backlog", "--max-backlog needs a value"),
    ("--ignore-size maybe", "--ignore-size takes no value"),
    ("-c", "-c is not a long flag"),
    ("max-backlog 5", "max-backlog is not a long flag"),
])
def test_rejects_bad_lines_naming_the_flag(line, fragment):
    config, errors = parse(line)
    assert config == {}
    assert len(errors) == 1 and fragment in errors[0]
    assert errors[0].startswith("Line 1:")


def test_rejects_duplicates_and_reports_every_bad_line():
    _, errors = parse("--max-backlog 1\n--max-backlog 2\n--nope")
    assert len(errors) == 2
    assert "Line 2" in errors[0] and "more than once" in errors[0]
    assert "Line 3" in errors[1]


def test_format_round_trips_with_and_without_option_list():
    config, _ = parse("--max-backlog 200000\n--buffer-size 32M\n--ignore-size\n--update")
    text = extra_flags.format_flags(config, OPTIONS)
    assert text.splitlines() == [
        "--max-backlog 200000", "--buffer-size 32M", "--ignore-size", "--update",
    ]
    assert parse(text)[0] == config
    # Without the list the names are guessed from the key.
    assert extra_flags.format_flags({"MaxBacklog": 5}) == "--max-backlog 5"


# --- Saving through the form ---

class OptionsRclone:
    """FakeRclone that can also answer options/info."""

    def __init__(self, fake, options=MAIN_OPTIONS, fail=False):
        self.fake, self.options, self.fail = fake, options, fail

    def __getattr__(self, name):
        return getattr(self.fake, name)

    def options_info(self):
        if self.fail:
            raise ConnectionError("rclone is down")
        return {"main": self.options}


@pytest.fixture()
def rclone(app, fake_rclone):
    client = OptionsRclone(fake_rclone)
    app.config["RCLONE_CLIENT"] = client
    return client


def saved_flags(app):
    with app.app_context():
        return Job.query.one().get_extra_flags()


def test_flags_are_saved_and_shown_back_as_cli_text(app, client, rclone):
    response = client.post(
        "/jobs/create", data={**JOB_FORM, "extra_flags": "--max-backlog 200000\n--ignore-size"}
    )
    assert response.status_code == 302
    assert saved_flags(app) == {"MaxBacklog": 200000, "IgnoreSize": True}
    with app.app_context():
        job_id = Job.query.one().id
        assert Job.query.one().to_rclone_config()["MaxBacklog"] == 200000
    page = client.get(f"/jobs/{job_id}/edit").get_data(as_text=True)
    assert "--max-backlog 200000\n--ignore-size</textarea>" in page


def test_bad_flags_show_inline_error_and_keep_typed_text(app, client, rclone):
    typed = "--max-bakclog 5\n--transfers 9"
    response = client.post("/jobs/create", data={**JOB_FORM, "extra_flags": typed})
    page = response.get_data(as_text=True)
    assert response.status_code == 400
    assert "--max-bakclog is not an rclone option" in page
    assert "--transfers is set by the Transfers field" in page
    assert "--max-bakclog 5\n--transfers 9</textarea>" in page
    assert 'value="Photos"' in page  # the rest of the form survives
    assert '<details class="card mb-3 jf-advanced" id="advanced" open>' in page
    with app.app_context():
        assert Job.query.count() == 0


def test_flags_fail_closed_when_rclone_is_unreachable(app, client, fake_rclone):
    app.config["RCLONE_CLIENT"] = OptionsRclone(fake_rclone, fail=True)
    response = client.post("/jobs/create", data={**JOB_FORM, "extra_flags": "--max-backlog 5"})
    assert response.status_code == 400
    assert "can&#39;t be checked" in response.get_data(as_text=True)
    with app.app_context():
        assert Job.query.count() == 0


def test_clearing_flags_needs_no_lookup_and_missing_field_keeps_them(app, client, fake_rclone):
    app.config["RCLONE_CLIENT"] = OptionsRclone(fake_rclone, fail=True)
    with app.app_context():
        db.session.add(Job(name="j", source="/a", destination="b:", sync_type="copy",
                           extra_flags=json.dumps({"MaxBacklog": 5})))
        db.session.commit()
        job_id = Job.query.one().id
    # A form without the field (older client) leaves the flags alone.
    client.post(f"/jobs/{job_id}/edit", data=JOB_FORM)
    assert saved_flags(app) == {"MaxBacklog": 5}
    client.post(f"/jobs/{job_id}/edit", data={**JOB_FORM, "extra_flags": "  "})
    assert saved_flags(app) == {}


# --- Form defaults ---

def test_new_job_defaults_to_copy_and_existing_keep_their_type(app, client, rclone):
    page = client.get("/jobs/create").get_data(as_text=True)
    assert 'value="copy" checked' in page
    assert 'value="sync" checked' not in page
    with app.app_context():
        db.session.add(Job(name="j", source="/a", destination="b:", sync_type="move"))
        db.session.commit()
    page = client.get("/jobs/1/edit").get_data(as_text=True)
    assert 'value="move" checked' in page


# --- Inline schedule ---

@pytest.fixture(autouse=True)
def running_scheduler(app):
    """A started scheduler replaces jobs by id; an unstarted one queues both."""
    scheduler = app.config["SCHEDULER_SERVICE"].scheduler
    scheduler.start(paused=True)
    yield
    scheduler.shutdown(wait=False)


def scheduler_job(app, schedule_id):
    return app.config["SCHEDULER_SERVICE"].scheduler.get_job(f"schedule_{schedule_id}")


def test_create_job_with_schedule_registers_it(app, client, rclone):
    client.post("/jobs/create", data={
        **JOB_FORM, "schedule_cron": "0 3 * * *", "schedule_enabled": "on",
    })
    with app.app_context():
        sched = Schedule.query.one()
        assert (sched.cron_expression, sched.enabled) == ("0 3 * * *", True)
        assert sched.job.name == "Photos"
        assert scheduler_job(app, sched.id)
    page = client.get("/schedules/").get_data(as_text=True)
    assert "Photos" in page


def test_edit_updates_disables_and_removes_schedule(app, client, rclone):
    client.post("/jobs/create", data={
        **JOB_FORM, "schedule_cron": "0 3 * * *", "schedule_enabled": "on",
    })
    with app.app_context():
        job_id = Job.query.one().id
        sched_id = Schedule.query.one().id

    client.post(f"/jobs/{job_id}/edit", data={
        **JOB_FORM, "schedule_cron": "30 4 * * 1", "schedule_enabled": "on",
    })
    with app.app_context():
        assert Schedule.query.one().cron_expression == "30 4 * * 1"
        trigger = str(scheduler_job(app, sched_id).trigger)
        assert "minute='30'" in trigger and "hour='4'" in trigger

    client.post(f"/jobs/{job_id}/edit", data={**JOB_FORM, "schedule_cron": "30 4 * * 1"})
    with app.app_context():
        assert Schedule.query.one().enabled is False
        assert scheduler_job(app, sched_id) is None

    client.post(f"/jobs/{job_id}/edit", data={**JOB_FORM, "schedule_cron": ""})
    with app.app_context():
        assert Schedule.query.count() == 0


def test_edit_without_schedule_fields_leaves_schedule_alone(app, client, rclone):
    client.post("/jobs/create", data={
        **JOB_FORM, "schedule_cron": "0 3 * * *", "schedule_enabled": "on",
    })
    client.post("/jobs/1/edit", data=JOB_FORM)
    with app.app_context():
        assert Schedule.query.one().cron_expression == "0 3 * * *"


def test_invalid_cron_is_an_inline_error_and_saves_nothing(app, client, rclone):
    response = client.post("/jobs/create", data={**JOB_FORM, "schedule_cron": "every day"})
    page = response.get_data(as_text=True)
    assert response.status_code == 400
    assert "Not a valid cron expression" in page
    assert 'value="every day"' in page
    with app.app_context():
        assert Job.query.count() == 0 and Schedule.query.count() == 0


def test_deleting_a_job_removes_its_scheduler_entry(app, client, rclone):
    client.post("/jobs/create", data={
        **JOB_FORM, "schedule_cron": "0 3 * * *", "schedule_enabled": "on",
    })
    with app.app_context():
        sched_id = Schedule.query.one().id
        assert scheduler_job(app, sched_id)
    client.post("/jobs/1/delete")
    with app.app_context():
        assert scheduler_job(app, sched_id) is None
        assert Schedule.query.count() == 0


def test_job_list_shows_schedule_in_words(app, client, rclone):
    client.post("/jobs/create", data={
        **JOB_FORM, "schedule_cron": "0 2 * * *", "schedule_enabled": "on",
    })
    page = client.get("/jobs/").get_data(as_text=True)
    assert "Every day at 02:00" in page
    assert "<code" in page and "0 2 * * *" in page
    assert 'aria-label="Start Photos"' in page
    assert "bg-info" not in page and "btn-success" not in page
