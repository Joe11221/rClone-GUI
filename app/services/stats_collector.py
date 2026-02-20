import json
import queue
import threading
import time


class StatsCollector:
    """Polls rclone core/stats and distributes updates to SSE subscribers."""

    def __init__(self, rclone_client, interval=2.0):
        self.rclone = rclone_client
        self.interval = interval
        self.subscribers = {}
        self._lock = threading.Lock()
        self._running = False
        self._thread = None

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False

    def subscribe(self, client_id):
        q = queue.Queue(maxsize=50)
        with self._lock:
            self.subscribers[client_id] = q
        return q

    def unsubscribe(self, client_id):
        with self._lock:
            self.subscribers.pop(client_id, None)

    def _poll_loop(self):
        while self._running:
            try:
                stats = self.rclone.get_stats()
                jobs = self.rclone.job_list()
                payload = json.dumps(
                    {"stats": stats, "jobs": jobs, "timestamp": time.time()}
                )
                with self._lock:
                    dead = []
                    for cid, q in self.subscribers.items():
                        try:
                            q.put_nowait(payload)
                        except queue.Full:
                            dead.append(cid)
                    for cid in dead:
                        del self.subscribers[cid]
            except Exception:
                # rclone may not be running yet
                pass
            time.sleep(self.interval)
