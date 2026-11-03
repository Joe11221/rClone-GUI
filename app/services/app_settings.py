"""Key/value settings edited in the UI (notification targets, auth, ...)."""

import json

from app.extensions import db
from app.models import AppSetting


def get_setting(key, default=None):
    row = db.session.get(AppSetting, key)
    if row is None:
        return default
    try:
        return json.loads(row.value)
    except ValueError:
        return default


def set_setting(key, value):
    """Store a JSON-serialisable value and commit."""
    row = db.session.get(AppSetting, key)
    if row is None:
        row = AppSetting(key=key)
        db.session.add(row)
    row.value = json.dumps(value)
    db.session.commit()
