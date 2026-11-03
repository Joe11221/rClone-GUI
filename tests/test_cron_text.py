import pytest

from app.services.cron_text import describe_cron


@pytest.mark.parametrize(
    "expr, text",
    [
        ("0 2 * * *", "Every day at 02:00"),
        ("0 4 * * 0", "Every Sunday at 04:00"),
        ("0 4 * * 7", "Every Sunday at 04:00"),
        ("0 4 * * sun", "Every Sunday at 04:00"),
        ("30 18 * * 1-5", "Weekdays at 18:30"),
        ("30 18 * * mon-fri", "Weekdays at 18:30"),
        ("0 9 * * 6,0", "Weekends at 09:00"),
        ("0 9 * * 1,3,5", "Every Monday, Wednesday and Friday at 09:00"),
        ("15 * * * *", "Every hour at :15"),
        ("*/15 * * * *", "Every 15 minutes"),
        ("* * * * *", "Every minute"),
        ("0 */6 * * *", "Every 6 hours, on the hour"),
        ("0 3 1 * *", "On the 1st of every month at 03:00"),
        ("5 3 22 * *", "On the 22nd of every month at 03:05"),
        ("0 3 13 * *", "On the 13th of every month at 03:00"),
        ("  0  2 * * *  ", "Every day at 02:00"),
    ],
)
def test_describes_common_cases(expr, text):
    assert describe_cron(expr) == text


@pytest.mark.parametrize(
    "expr",
    ["0 2 * 6 *", "0 2 1 * 1", "0 0-5 * * *", "@daily", "0 2 * *", "99 2 * * *",
     "0 25 * * *", "0 2 * * 9", "*/90 * * * *", "0 2 32 * *", "abc", ""],
)
def test_falls_back_to_raw_expression(expr):
    assert describe_cron(expr) == expr.strip()


def test_none_is_empty():
    assert describe_cron(None) == ""
