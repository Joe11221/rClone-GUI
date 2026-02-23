import json
import queue
import threading
import time


class StatsCollector:
    """Polls rclone stats ONLY for runs our app explicitly started."""

    def __init__(self, rclone_client, job_runner, interval=2.0):
        self.rclone = rclone_client
        self.job_runner = job_runner
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
                # Only fetch stats for runs OUR app started
                active_runs = dict(self.job_runner._active_runs)
                run_stats = {}
                total_speed = 0
                total_bytes = 0

                for run_id, rclone_jobid in active_runs.items():
                    stats_group = f"job_{run_id}"
                    try:
                        stats = self.rclone.get_stats(group=stats_group)
                        run_stats[str(run_id)] = {
                            "stats": stats,
                            "rclone_jobid": rclone_jobid,
                        }
                        total_speed += stats.get("speed", 0) or 0
                        total_bytes += stats.get("bytes", 0) or 0
                    except Exception:
                        pass

                payload = json.dumps(
                    {
                        "runs": run_stats,
                        "active_count": len(active_runs),
                        "total_speed": total_speed,
                        "total_bytes": total_bytes,
                        "timestamp": time.time(),
                    }
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
                pass
            time.sleep(self.interval)
