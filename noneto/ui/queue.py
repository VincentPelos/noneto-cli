"""Queue tab: the songs after the current one."""
from rich.text import Text
from textual.containers import VerticalScroll
from textual.widgets import Static

from ..i18n import _
from .common import artist, clean

CSS = """
#rail { height: 1fr; padding: 0 1; scrollbar-size: 1 1; }
#up-next { height: auto; color: $text-muted; }
"""
SHOWN = 100  # longer queues end with "+ N songs"


class QueueList(VerticalScroll):
    def compose(self):
        yield Static(_("No songs in the queue."), id="up-next", markup=False)

    def show(self, next_tracks):
        up_next = self.query_one("#up-next", Static)
        if not next_tracks:
            up_next.update(_("No songs in the queue."))
            return
        text = Text()
        for number, queued in enumerate(next_tracks[:SHOWN], 1):
            if number > 1:
                text.append("\n")
            text.append(f"{number:>3}  ", style="dim")
            text.append(clean(queued.name), style="bold")
            text.append(f" · {artist(queued)}", style="dim")
        if len(next_tracks) > SHOWN:
            text.append("\n\n" + _("+ {count} songs", count=len(next_tracks) - SHOWN))
        up_next.update(text)
