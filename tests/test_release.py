"""Release boundaries: source identity, version coherence, privacy and signing state."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

SPEC = importlib.util.spec_from_file_location("noneto_release_tools", Path(__file__).resolve().parents[1] / "packaging/release.py")
release = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(release)


class ReleaseTests(unittest.TestCase):
    def source(self, path, version="2.3.4", windows=True):
        path.mkdir(parents=True, exist_ok=True)
        (path / "pyproject.toml").write_text(
            f'[project]\nname="noneto-cli"\nversion={json.dumps(version)}\nrequires-python=">=3.12,<3.13"\ndependencies=[]\n',
            encoding="utf-8")
        (path / "noneto").mkdir()
        (path / "noneto/app.py").write_text('if sys.platform != "win32":\n    raise SystemExit(1)\n' if windows else "pass\n")
        return path

    def test_version_comes_from_selected_source(self):
        with tempfile.TemporaryDirectory() as directory:
            source = self.source(Path(directory))
            self.assertEqual(release.project(source), "2.3.4")
            resource = release.version_resource(release.project(source))
            self.assertIn("(2, 3, 4, 0)", resource)
            self.assertIn("ProductName', 'Noneto CLI'", resource)
            self.assertIn("ProductVersion', '2.3.4'", resource)

    def test_development_source_is_rejected_before_build(self):
        with tempfile.TemporaryDirectory() as directory:
            source = self.source(Path(directory), windows=False)
            with self.assertRaisesRegex(ValueError, "Windows production"):
                release.project(source)

    def test_version_cannot_inject_build_code_or_overflow_pe_metadata(self):
        for version in ("1.2.3rc1", "1.2.3\nmalicious", "1.2.65536"):
            with self.subTest(version=version), tempfile.TemporaryDirectory() as directory:
                source = self.source(Path(directory), version=version)
                with self.assertRaises(ValueError):
                    release.project(source)

    def test_packages_cannot_include_private_or_traversal_paths(self):
        for member in ("memoria/private.md", "../outside.txt", "noneto/../../outside.txt", "AGENTS.md"):
            with self.subTest(member=member), tempfile.TemporaryDirectory() as directory:
                archive = Path(directory) / "package.whl"
                with zipfile.ZipFile(archive, "w") as output:
                    output.writestr(member, "test fixture")
                with self.assertRaises(ValueError):
                    release.audit_archive(archive)

    def test_manifest_hashes_final_bytes_without_modifying_packages(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = self.source(root / "source")
            output = root / "output"
            output.mkdir()
            wheel = output / "noneto_cli-2.3.4-py3-none-any.whl"
            installer = output / "Noneto-CLI-2.3.4-windows-x64-setup.exe"
            wheel.write_bytes(b"immutable package fixture")
            installer.write_bytes(b"before signing")
            first = release.checksums(source, output, "source-commit")
            installer.write_bytes(b"after signing")
            with patch.object(release, "verify_signature") as verify:
                final = release.checksums(source, output, "source-commit", signed=True)
            verify.assert_called_once_with(installer)
            self.assertEqual(final["files"][wheel.name], first["files"][wheel.name])
            self.assertNotEqual(final["files"][installer.name], first["files"][installer.name])
            self.assertEqual(final["windows_signing"], "signed")
            self.assertEqual(json.loads((output / "RELEASE-MANIFEST.json").read_text()), final)

    def test_failed_signature_verification_never_writes_signed_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = self.source(root / "source")
            output = root / "output"
            output.mkdir()
            (output / "Noneto-CLI-2.3.4-windows-x64-setup.exe").touch()
            with patch.object(release, "verify_signature", side_effect=ValueError("Untrusted signature")):
                with self.assertRaises(ValueError):
                    release.checksums(source, output, "source-commit", signed=True)
            self.assertFalse((output / "RELEASE-MANIFEST.json").exists())

    def test_upstream_libraries_are_not_signing_targets(self):
        with self.assertRaisesRegex(ValueError, "Only the Noneto"):
            release.verify_signature(Path("python312.dll"))

    def test_bundle_version_mismatch_stops_before_compilation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = self.source(root / "source")
            bundle = root / "work/dist/noneto"
            bundle.mkdir(parents=True)
            (bundle / "BUILD-INFO.json").write_text('{"version":"1.0.0"}')
            with patch.object(release, "run") as run:
                with self.assertRaisesRegex(ValueError, "versions differ"):
                    release.installer(source, root / "work", root / "output")
            run.assert_not_called()
