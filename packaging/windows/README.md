# Release build layout

`packaging/release.py` is the shared builder for Python distributions and Windows Setup.
It reads the selected checkout's `pyproject.toml`; there are no version overrides or source
rewrites. It rejects development sources without the production Windows startup guard.

| Input | Purpose |
| --- | --- |
| `../toolchain.json` | Tested Python, uv and pinned Inno Setup installer/hash |
| `../runtime.lock` | Windows runtime and native-test dependencies, with hashes |
| `requirements.lock` | Build tools, with hashes |
| `noneto.spec`, `entry.py`, `bundle_checks.py` | Console bundle, resources and offline checks |
| `noneto.iss` | Per-user Setup, mpv preparation and uninstall |
| `../signpath/` | Restricted signing configurations for product-owned files |

From the **public Windows source checkout**, install the Python and uv versions declared
in `toolchain.json`, then run:

```powershell
python packaging/release.py all --work build/release --output dist/candidate
```

The builder creates a dedicated environment, installs hash-locked dependencies, builds the
wheel/sdist and console bundle, exercises English/Spanish help and native dependency loading,
and compiles Inno Setup. Inno Setup is downloaded from its pinned official release and
hash-checked if `--iscc PATH` is not supplied. No user TIDAL account is required for building.

The final directory contains the wheel, sdist, installer, separate Python/Windows checksums,
and `RELEASE-MANIFEST.json` with the source commit, source version, hashes and signing state.
Building does not publish files, request a certificate, or change an existing release.

## Signing order

The public manual workflow builds on GitHub-hosted Windows runners. Unsigned builds are
the default. After provider approval/configuration, signing follows this order:

1. Build and upload `noneto.exe` as a GitHub workflow artifact.
2. Request its signature, wait for manual provider approval, and verify trust/timestamp.
3. Compile Setup using that signed application, then upload Setup as a workflow artifact.
4. Request and verify Setup's signature; only then compute final checksums.

See [Code signing policy](../../CODE_SIGNING.md) for the current approval state, upstream
binary exclusions and the outstanding unsigned-uninstaller review. A signed file is not a
promise that SmartScreen will immediately recognize a new publisher.

## Installation behavior

Setup includes a private Python runtime and Python/native libraries. It does not reuse
global Python. The uv route reuses a compatible Python 3.12 or prepares one automatically.
Both routes use Noneto's own pinned mpv and reuse the copy at their managed location.

Setup installs under `%LOCALAPPDATA%\Programs\Noneto CLI`, without administrator privileges.
It prepares mpv over HTTPS and verifies its pinned SHA-256 before copying the application.
No mpv archive is redistributed in Setup. Start menu integration and optional PATH/desktop
tasks are documented by the wizard. Uninstall removes application files but preserves
`%LOCALAPPDATA%\Noneto` session/preferences/logs. Existing uv installations stay separate.

WebView2/.NET are Windows components. Browser/clipboard sign-in remains available when
WebView2 is unavailable. Builds and automated checks do not authorize a real TIDAL account.
