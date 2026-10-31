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
