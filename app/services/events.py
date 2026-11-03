"""In-process events, so the runner and health checks don't call notifiers directly.

Listeners run synchronously in the emitter's greenlet. A listener that raises
is logged and skipped; it never breaks the code that emitted the event.

Events and their data (all keyword arguments):

``run_finished``
    run_id, job_id, job_name, status (completed | failed | stopped |
    interrupted), errors (int), error_message (str or None), guard (str or
    None), will_retry (bool: an automatic retry has been scheduled)
``run_held``
    run_id, job_id, job_name, guard (source_empty | source_unreachable),
    reason (str, readable)
``run_skipped``
    run_id, job_id, job_name, reason (str)
``health_changed``
    job_id, job_name, old_state, new_state, detail (str, readable)
"""

import logging

logger = logging.getLogger(__name__)


class EventBus:
    def __init__(self):
        self._listeners = []

    def subscribe(self, listener):
        """``listener(event, data)`` is called for every emitted event."""
        self._listeners.append(listener)

    def emit(self, event, **data):
        for listener in list(self._listeners):
            try:
                listener(event, data)
            except Exception:
                logger.exception("Listener %r failed on %s", listener, event)
