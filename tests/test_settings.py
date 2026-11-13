import json
from pathlib import Path
import tempfile
import unittest

from noneto.settings import load_settings, save_settings

THEMES = ("noneto-dark", "dracula")
MODES = ("classic", "braille", "mirror")


class SettingsTests(unittest.TestCase):
    def test_saved_theme_is_restored(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "Noneto" / "settings.json"
            self.assertEqual(load_settings(THEMES, path), {})
            save_settings({"theme": "dracula", "visualizer_mode": "braille"}, path)
            self.assertEqual(load_settings(THEMES, path), {"theme": "dracula"})
            self.assertEqual(load_settings(THEMES, path, MODES),
                             {"theme": "dracula", "visualizer_mode": "braille"})
            self.assertEqual([p.name for p in path.parent.iterdir()], ["settings.json"])

    def test_invalid_or_corrupt_settings_fall_back_to_defaults(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            for content in (b"{", b"[]", json.dumps({"theme": "removed-theme"}).encode(),
                            json.dumps({"theme": 3}).encode(), b"x" * 10000):
                path.write_bytes(content)
                self.assertEqual(load_settings(THEMES, path, MODES), {})
            path.write_text(json.dumps({"theme": "dracula", "visualizer_mode": "unknown", "extra": "dropped"}),
                            encoding="utf-8")
            self.assertEqual(load_settings(THEMES, path, MODES), {"theme": "dracula"})

    def test_lyrics_layout_is_restored_only_when_known(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            save_settings({"lyrics_layout": "cover"}, path)
            self.assertEqual(load_settings(THEMES, path, allowed_lyrics_layouts=("lyrics", "cover")),
                             {"lyrics_layout": "cover"})
            self.assertEqual(load_settings(THEMES, path), {})
            save_settings({"lyrics_layout": "giant"}, path)
            self.assertEqual(load_settings(THEMES, path, allowed_lyrics_layouts=("lyrics", "cover")), {})


if __name__ == "__main__":
    unittest.main()
