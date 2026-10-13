import ast
import os
from pathlib import Path
import subprocess
import sys
import unittest

from noneto import i18n
from noneto.i18n import ES, _, set_language


class I18nTests(unittest.TestCase):
    def setUp(self):
        set_language("en")

    def tearDown(self):
        set_language("en")

    def test_every_source_message_has_a_spanish_translation(self):
        root = Path(__file__).resolve().parents[1] / "noneto"
        missing = []
        for path in root.rglob("*.py"):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if (isinstance(node, ast.Call) and getattr(node.func, "id", None) == "_" and node.args
                        and isinstance(node.args[0], ast.Constant) and node.args[0].value not in ES):
                    missing.append(f"{path.name}: {node.args[0].value}")
        self.assertEqual(missing, [])
        for english, spanish in ES.items():
            fields = lambda text: sorted(part.split("}")[0] for part in text.split("{")[1:])
            self.assertEqual(fields(english), fields(spanish), english)

    def test_english_is_the_source_and_spanish_is_optional(self):
        self.assertEqual(i18n.DEFAULT_LANGUAGE, "en")
        self.assertEqual(_("Queue · {count}", count=3), "Queue · 3")
        set_language("es")
        self.assertEqual(_("Queue · {count}", count=3), "Cola · 3")
        self.assertEqual(_("Search"), "Buscar")
        self.assertEqual(_("Search results"), "Búsqueda")
        self.assertEqual(_("untranslated text"), "untranslated text")
        with self.assertRaises(ValueError):
            set_language("fr")

    def test_cli_defaults_to_english_even_with_a_spanish_locale(self):
        root = Path(__file__).resolve().parents[1]
        environment = {**os.environ, "LANG": "es_ES.UTF-8", "LC_ALL": "es_ES.UTF-8",
                       "PYTHONIOENCODING": "utf-8"}
        for options, expected in (([], "unofficial TIDAL client"),
                                  (["--lang", "es"], "cliente no oficial de TIDAL")):
            result = subprocess.run([sys.executable, "-m", "noneto", *options, "--help"],
                                    cwd=root, capture_output=True, text=True, encoding="utf-8",
                                    timeout=30, env=environment)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(expected, result.stdout)

    def test_tui_renders_in_english_and_explicit_spanish(self):
        # Use fresh interpreters because bindings and tab labels are created at import time.
        script = """
import asyncio, sys
from unittest.mock import Mock
from noneto.i18n import set_language
if len(sys.argv) > 1:
    set_language(sys.argv[1])
from noneto.audio.controller import PlaybackState
from noneto.ui.tui import NonetoApp

async def run():
    library = Mock()
    library.submit.side_effect = lambda *args: asyncio.get_running_loop().create_future()
    app = NonetoApp(library, Mock(snapshot=Mock(return_value=PlaybackState())))
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause(0.4)
        print(app.query_one("#search-button").label)
        print(app.query_one("#quality").content)
        print(app._views.get_tab("queue-view").label.plain)
        print(app.query_one("#mode").content)
asyncio.run(run())
"""
        root = Path(__file__).resolve().parents[1]
        for options, expected, status in (([], ["Search", "Requested: Hi-Res", "Queue"], "Not playing"),
                                         (["es"], ["Buscar", "Pedido: Hi-Res", "Cola"], "Sin reproduccion")):
            result = subprocess.run([sys.executable, "-c", script, *options], cwd=root,
                                    capture_output=True, text=True, encoding="utf-8", timeout=60,
                                    env={**os.environ, "PYTHONIOENCODING": "utf-8"})
            self.assertEqual(result.returncode, 0, result.stderr)
            lines = result.stdout.splitlines()
            self.assertEqual(lines[:3], expected)
            self.assertIn(status, lines[3])


if __name__ == "__main__":
    unittest.main()
