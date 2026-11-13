"""Bounded priority worker; iterator jobs yield ownership between pages."""
from concurrent.futures import Future, InvalidStateError
import heapq
import inspect
import itertools
import threading

from ..errors import AppError
from ..i18n import _


class PriorityExecutor:
    def __init__(self, name, capacity=128):
        self._condition = threading.Condition()
        self._jobs = []
        self._sequence = itertools.count()
        self._capacity = capacity
        self._closing = False
        self._thread = threading.Thread(target=self._run, name=name, daemon=True)
        self._thread.start()

    def submit(self, function, *args, priority=20):
        future = Future()
        with self._condition:
            if self._closing:
                raise RuntimeError("Executor is closed")
            self._jobs = [job for job in self._jobs if not job[2].cancelled() or job[5] is not None]
            heapq.heapify(self._jobs)
            if len(self._jobs) >= self._capacity:
                raise AppError(_("Too many pending actions. Wait a moment."))
            heapq.heappush(self._jobs, (priority, next(self._sequence), future, function, args, None))
            self._condition.notify()
        return future

    def _run(self):
        while True:
            with self._condition:
                self._condition.wait_for(lambda: self._closing or self._jobs)
                if not self._jobs:
                    return
                priority, _, future, function, args, iterator = heapq.heappop(self._jobs)
            if future.cancelled():
                if iterator is not None:
                    iterator.close()
                continue
            try:
                if iterator is None:
                    # Page jobs remain cancellable between SDK requests.
                    if not inspect.isgeneratorfunction(function):
                        if not future.set_running_or_notify_cancel():
                            continue
                        future.set_result(function(*args))
                        continue
                    iterator = function(*args)
                next(iterator)
            except StopIteration as done:
                try:
                    future.set_result(done.value)
                except InvalidStateError:
                    pass
            except Exception as error:
                try:
                    future.set_exception(error)
                except InvalidStateError:
                    pass
            else:
                with self._condition:
                    if self._closing or future.cancelled():
                        future.cancel()
                        iterator.close()
                    else:
                        heapq.heappush(self._jobs, (priority, next(self._sequence), future, function, args, iterator))
                        self._condition.notify()

    def shutdown(self, wait=True, cancel_futures=True):
        with self._condition:
            self._closing = True
            if cancel_futures:
                for job in self._jobs:
                    job[2].cancel()
            self._condition.notify_all()
        if wait:
            self._thread.join()
