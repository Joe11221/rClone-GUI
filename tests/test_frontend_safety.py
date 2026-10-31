import re
from pathlib import Path

STATIC_JS = Path(__file__).resolve().parent.parent / "app" / "static" / "js"


def test_dashboard_never_injects_filenames_via_innerhtml():
    """Regression: file names are untrusted and must be set with textContent."""
    source = (STATIC_JS / "dashboard.js").read_text(encoding="utf-8")
    for match in re.finditer(r"innerHTML\s*=", source):
        statement = source[match.start(): source.index(";", match.start())]
        assert "t.name" not in statement
    assert "nameSpan.textContent = t.name" in source


def test_no_native_confirm_dialogs():
    """confirm() titles itself with the page address, even in the Windows app."""
    app_dir = STATIC_JS.parent.parent
    for path in [*app_dir.glob("templates/**/*.html"), *STATIC_JS.glob("*.js")]:
        assert not re.search(r"\bconfirm\(", path.read_text(encoding="utf-8")), path
