"""One prioritized SDK owner and a separate bounded image worker."""
from concurrent.futures import Future, InvalidStateError

from .covers import COVER_SIZE, CoverDownloader, cover_image
from .executor import PriorityExecutor


class Library:
    def __init__(self, catalog, persist=lambda: None):
        self.catalog, self.persist = catalog, persist
        self.executor = PriorityExecutor("noneto-api")
        self._media = PriorityExecutor("noneto-covers", capacity=32)
        self._covers = CoverDownloader()

    def submit(self, operation, *args):
        if operation in ("artwork", "media_cover") and callable(getattr(self.catalog, "cover_request", None)):
            return self._cover(operation, args)
        if operation in ("tracks", "playlists") and callable(getattr(self.catalog, "pages", None)):
            return self.executor.submit(self._pages, operation, args)
        priority = 10 if operation in ("lyrics", "create", "add") else 20
        return self.executor.submit(self._call, getattr(self.catalog, operation), args, priority=priority)

    def resolve(self, resolver, track, directory):
        return self.executor.submit(self._call, self._resolve, (resolver, track, directory), priority=0)

    def _resolve(self, resolver, track, directory):
        if hasattr(self.catalog, "hydrate"):
            track = self.catalog.hydrate(track)
        return resolver(track, directory)

    def _call(self, function, args):
        try:
            return function(*args)
        finally:
            self.persist()

    def _pages(self, operation, args):
        pages = self.catalog.pages(operation, *args)
        result = []
        try:
            while True:
                try:
                    page = self._call(next, (pages,))
                except StopIteration:
                    return result
                result.extend(page)
                yield  # A pending stream can run before the next page.
        finally:
            pages.close()

    def _cover(self, operation, args):
        result = Future()
        # "artwork" (decoded for the TUI) shares the download and cache entry with the media controls.
        resolution = args[1] if len(args) > 1 else COVER_SIZE
        preparation = self.executor.submit(self._call, self.catalog.cover_request, (args[0], resolution), priority=5)
        stages = [preparation]

        def complete(pending):
            if result.cancelled():
                return
            try:
                if pending.cancelled():
                    result.cancel()
                else:
                    result.set_result(pending.result())
            except InvalidStateError:
                pass
            except Exception as error:
                try:
                    result.set_exception(error)
                except InvalidStateError:
                    pass

        def download(pending):
            if result.cancelled():
                return
            try:
                if pending.cancelled():
                    result.cancel()
                    return
                request = pending.result()

                def fetch():
                    data = self._covers.fetch(request)
                    return cover_image(data) if operation == "artwork" else data

                transfer = self._media.submit(fetch)
                stages.append(transfer)
                if result.cancelled():
                    transfer.cancel()
                transfer.add_done_callback(complete)
            except Exception as error:
                try:
                    result.set_exception(error)
                except InvalidStateError:
                    pass

        def cancel(pending):
            if pending.cancelled():
                for stage in stages:
                    stage.cancel()

        result.add_done_callback(cancel)
        preparation.add_done_callback(download)
        return result

    def close(self):
        self.executor.shutdown(wait=True, cancel_futures=True)
        self._media.shutdown(wait=True, cancel_futures=True)
        self._covers.close()
