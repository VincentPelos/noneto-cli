"""Serialize SDK access, including OAuth refresh, away from the UI thread."""
from concurrent.futures import ThreadPoolExecutor


class Library:
    def __init__(self, catalog, persist=lambda: None):
        self.catalog = catalog
        self.persist = persist
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="noneto-api")

    def submit(self, operation, *args):
        return self.executor.submit(self._call, getattr(self.catalog, operation), args)

    def resolve(self, resolver, track, directory):
        return self.executor.submit(self._call, resolver, (track, directory))

    def _call(self, function, args):
        try:
            return function(*args)
        finally:
            self.persist()

    def close(self):
        self.executor.shutdown(wait=True, cancel_futures=True)
