import ast
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import Mock, patch

from noneto import i18n
from noneto.i18n import EN, _, set_language


class I18nTests(unittest.TestCase):
    def tearDown(self):
        set_language("es")

    def test_every_translated_text_has_an_english_version(self):
        root = Path(__file__).resolve().parents[1] / "noneto"
        missing = []
        for path in root.rglob("*.py"):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if (isinstance(node, ast.Call) and getattr(node.func, "id", None) == "_" and node.args
                        and isinstance(node.args[0], ast.Constant) and node.args[0].value not in EN):
                    missing.append(f"{path.name}: {node.args[0].value}")
        self.assertEqual(missing, [])
        for spanish, english in EN.items():
            fields = lambda text: sorted(part.split("}")[0] for part in text.split("{")[1:])
            self.assertEqual(fields(spanish), fields(english), spanish)

    def test_spanish_is_the_source_and_english_is_looked_up(self):
        self.assertEqual(_("Cola · {count}", count=3), "Cola · 3")
        set_language("en")
        self.assertEqual(_("Cola · {count}", count=3), "Queue · 3")
        self.assertEqual(_("texto sin traduccion"), "texto sin traduccion")
        with self.assertRaises(ValueError):
            set_language("fr")

    def test_system_language_follows_windows_display_language(self):
        for lang_id, expected in [(0x0C0A, "es"), (0x080A, "es"), (0x0409, "en"), (0x0416, "en")]:
            kernel = Mock(GetUserDefaultUILanguage=Mock(return_value=lang_id))
            with patch.object(i18n.ctypes, "windll", Mock(kernel32=kernel), create=True):
                self.assertEqual(i18n.system_language(), expected)

    def test_tui_renders_in_english(self):
        # Its labels are built at import time, so English needs a fresh interpreter (as app.main does).
        script = """
import asyncio
from unittest.mock import Mock
from noneto.i18n import set_language
set_language("en")
from noneto.audio.controller import PlaybackState
from noneto.ui.tui import NonetoApp

async def run():
    library = Mock()
    library.submit.side_effect = lambda *args: asyncio.get_running_loop().create_future()
    app = NonetoApp(library, Mock(snapshot=Mock(return_value=PlaybackState())))
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause(0.4)  # the status line fills on the first 0.25 s refresh
        print(app.query_one("#search-button").label)
        print(app.query_one("#quality").content)
        print(app._views.get_tab("queue-view").label.plain)
        print(app.query_one("#mode").content)
asyncio.run(run())
"""
        root = Path(__file__).resolve().parents[1]
        result = subprocess.run([sys.executable, "-c", script], cwd=root, capture_output=True, text=True,
                                encoding="utf-8", timeout=60, env={**os.environ, "PYTHONIOENCODING": "utf-8"})
        self.assertEqual(result.returncode, 0, result.stderr)
        lines = result.stdout.splitlines()
        self.assertEqual(lines[:3], ["Search", "Requested: Hi-Res", "Queue"])
        self.assertIn("Not playing", lines[3])

if __name__ == "__main__":
    unittest.main()
