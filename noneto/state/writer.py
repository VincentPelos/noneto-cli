"""One disk writer with one replaceable pending immutable queue snapshot."""
import threading


class QueueWriter:
    def __init__(self, store, on_error):
        self.store, self.on_error = store, on_error
        self._condition = threading.Condition()
        self._pending = None
        self._closing = False
        self._thread = threading.Thread(target=self._run, name="noneto-queue", daemon=True)
        self._thread.start()

    def submit(self, state, tracks, order):
        # The controller replaces these tuples; it never edits their ordering.
        with self._condition:
            if self._closing:
                raise RuntimeError("Queue writer is closed")
            self._pending = (state, tracks, order)
            self._condition.notify()

    def _run(self):
        while True:
            with self._condition:
                self._condition.wait_for(lambda: self._closing or self._pending is not None)
                if self._pending is None:
                    return
                pending, self._pending = self._pending, None
            try:
                self.store.save(*pending)
            except Exception as error:
                self.on_error(error)

    def close(self):
        with self._condition:
            self._closing = True
            self._condition.notify_all()
        self._thread.join()  # Includes the newest pending state, even after a failed write.
