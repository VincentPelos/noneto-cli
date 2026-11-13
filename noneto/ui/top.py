"""Top bar: logo, Library ▾, search box and the quality badge."""
from textual.containers import Horizontal
from textual.widgets import Button, Input, Static

from ..i18n import _
from .common import clean
from .quality import QualityBadge

CSS = """
#top { height: 5; background: $panel; padding: 1 0; }
#top > #quality { margin-top: 1; }
#top > Button { margin: 1 1 0 0; }
#brand { width: auto; height: 3; padding: 0 1 0 2; text-style: bold; }
#library { max-width: 32; background: $surface; }
#search { width: 1fr; height: 3; border: round $border; padding: 0 1; margin-right: 1; background: $panel; }
#search:focus { border: round $primary; background: $panel; }
#search-button { min-width: 8; }
"""

# Noneto symbol (docs/assets/noneto-symbol-*.svg) in half blocks: 6×6 pixels over the 3-row top bar.
# The symbol is the N of NONETO, as in the logo.
BRAND = "██▄  █\n█ ▀▄ █ ONETO\n█  ▀██"


class TopBar(Horizontal):
    """Layout only: the app handles Library, Search and the badge updates."""

    def __init__(self, user_name, quality, **kwargs):
        super().__init__(**kwargs)
        self.user_name, self.quality = user_name, quality

    def compose(self):
        yield Static(BRAND, id="brand", markup=False)
        library = Button(_("Library ▾"), id="library")
        library.tooltip = _("{name}'s playlists · Ctrl+L", name=clean(self.user_name or _("User")))
        yield library
        yield Input(placeholder=_("Search songs or playlists…"), max_length=200, id="search")
        yield Button(_("Search"), id="search-button", variant="primary")
        yield QualityBadge(self.quality, id="quality")
