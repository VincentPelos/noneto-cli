"""Modal dialogs: the library picker, creating a playlist and choosing where to add a song."""
from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, ListItem, ListView, Static

from ..i18n import _
from .common import clean

CSS = """
ModalScreen { align: center middle; background: $background 65%; }
#dialog { width: 60; max-width: 90%; height: auto; max-height: 85%; padding: 1 2; background: $panel; border: solid $border; }
.dialog-title { height: 2; text-style: bold; }
.dialog-actions { height: 1; margin-top: 1; }
#playlists, #destinations { height: auto; max-height: 18; margin-bottom: 1; }
#dialog ListItem { padding: 0 1; }
"""


class LibraryScreen(ModalScreen):
    """Playlist picker that replaces the old sidebar; creating and refreshing live here too."""
    BINDINGS = [("escape", "dismiss(None)", _("Cancel"))]

    def __init__(self, playlists, writing=False):
        super().__init__()
        self.playlists = playlists
        self.writing = writing

    def compose(self):
        with Vertical(id="dialog"):
            yield Label(_("Your library"), classes="dialog-title")
            if self.playlists:
                yield ListView(*(ListItem(Label(Text(clean(p.name)))) for p in self.playlists), id="playlists")
            else:
                yield Static(_("No playlists. Create one or refresh."), id="empty-library", markup=False)
            with Horizontal(classes="dialog-actions"):
                yield Button(_("+ New playlist"), id="new-playlist", variant="primary", disabled=self.writing)
                yield Button(_("Refresh"), id="refresh")
                yield Button(_("Close"), id="cancel")

    def on_mount(self):
        if self.playlists:
            self.query_one(ListView).focus()

    @on(ListView.Selected)
    def choose(self, event):
        if event.list_view.index is not None:
            self.dismiss(("open", self.playlists[event.list_view.index]))

    @on(Button.Pressed)
    def pressed(self, event):
        event.stop()
        self.dismiss({"new-playlist": ("new", None), "refresh": ("refresh", None)}.get(event.button.id))


class CreatePlaylist(ModalScreen):
    BINDINGS = [("escape", "dismiss(None)", _("Cancel"))]

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label(_("New playlist"), classes="dialog-title")
            yield Input(placeholder=_("Your playlist's name"), max_length=100, id="playlist-name")
            with Horizontal(classes="dialog-actions"):
                yield Button(_("Cancel"), id="cancel")
                yield Button(_("Create"), id="confirm", variant="primary")

    def on_mount(self):
        self.query_one(Input).focus()

    @on(Input.Submitted)
    @on(Button.Pressed, "#confirm")
    def confirm(self):
        title = self.query_one(Input).value.strip()
        if title:
            self.dismiss(title)

    @on(Button.Pressed, "#cancel")
    def cancel(self):
        self.dismiss(None)


class ChoosePlaylist(ModalScreen):
    BINDINGS = [("escape", "dismiss(None)", _("Cancel"))]

    def __init__(self, playlists):
        super().__init__()
        self.playlists = playlists

    def compose(self):
        with Vertical(id="dialog"):
            yield Label(_("Add to one of your playlists"), classes="dialog-title")
            yield ListView(*(ListItem(Label(Text(clean(p.name)))) for p in self.playlists), id="destinations")
            yield Button(_("Cancel"), id="cancel")

    def on_mount(self):
        self.query_one(ListView).focus()

    @on(ListView.Selected)
    def choose(self, event):
        if event.list_view.index is not None:
            self.dismiss(self.playlists[event.list_view.index])

    @on(Button.Pressed, "#cancel")
    def cancel(self):
        self.dismiss(None)
