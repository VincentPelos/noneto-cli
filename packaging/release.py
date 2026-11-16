"""Build reviewed Windows sources. One version, one recipe, no publication or credentials.

Public checkout: python packaging/release.py all
The source version and platform policy are read unchanged from the selected checkout.
"""
import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tomllib
import urllib.request
import zipfile
import tarfile

PACKAGING = Path(__file__).resolve().parent
ROOT = PACKAGING.parent
PRODUCT = "Noneto CLI"
PUBLISHER = "VincentPelos"


def run(*args, **kwargs):
    subprocess.run([str(arg) for arg in args], check=True, **kwargs)


def sha256(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def project(source):
    data = tomllib.loads((source / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    version = data["version"]
    if not re.fullmatch(r"\d+\.\d+\.\d+", version) or any(int(p) > 65535 for p in version.split(".")):
        raise ValueError("Release version must be a stable major.minor.patch supported by Windows")
    app = (source / "noneto/app.py").read_text(encoding="utf-8")
    if (data["name"] != "noneto-cli" or data["requires-python"] != ">=3.12,<3.13"
            or 'sys.platform != "win32"' not in app
            or any("PyQt" in dep or "keyring" in dep for dep in data["dependencies"])):
        raise ValueError("Build only the reviewed Windows production source, never development main")
    return version


def mpv_manifest(source):
    values = {}
    for statement in ast.parse((source / "noneto/audio/mpv_install.py").read_text(encoding="utf-8")).body:
        if isinstance(statement, ast.Assign):
            for target in statement.targets:
                if isinstance(target, ast.Name) and target.id in {"MPV_URL", "MPV_SHA256"}:
                    values[target.id] = ast.literal_eval(statement.value)
    if (not values.get("MPV_URL", "").startswith("https://downloads.sourceforge.net/project/mpv-player-windows/64bit/")
            or not re.fullmatch(r"[0-9a-f]{64}", values.get("MPV_SHA256", ""))):
        raise ValueError("Expected Noneto's pinned upstream mpv URL and SHA-256")
    return values


def environment(source, work):
    uv = shutil.which("uv")
    if not uv:
        raise SystemExit("Install uv on the build machine")
    config = json.loads((PACKAGING / "toolchain.json").read_text(encoding="utf-8"))
    if sys.version.split()[0] != config["python"] or sys.platform != "win32":
        raise SystemExit(f"Build on Windows with Python {config['python']} x64")
    import struct
    if struct.calcsize("P") != 8:
        raise SystemExit("An x64 Python interpreter is required")
    python = work / "env/Scripts/python.exe"
    if not python.exists():
        run(uv, "venv", "--python", sys.executable, work / "env")
    run(uv, "pip", "sync", "--python", python, "--require-hashes",
        PACKAGING / "runtime.lock", PACKAGING / "windows/requirements.lock")
    run(uv, "pip", "install", "--python", python, "--no-deps", "--no-build-isolation", source)
    run(uv, "pip", "check", "--python", python)
    return uv, python


def audit_archive(path):
    """Reject private context and unsafe members without extracting an archive."""
    if path.suffix == ".whl":
        with zipfile.ZipFile(path) as archive:
            entries = [(name, archive.read(name)) for name in archive.namelist() if not name.endswith("/")]
    else:
        with tarfile.open(path) as archive:
            entries = [(m.name, archive.extractfile(m).read()) for m in archive.getmembers() if m.isfile()]
    for name, data in entries:
        parts = Path(name).parts
        if Path(name).is_absolute() or ".." in parts or any(p in {"memoria", "graphify-out", "AGENTS.md", ".venv"} for p in parts):
            raise ValueError(f"Non-public or unsafe package member: {name}")
        if str(ROOT.resolve()).encode() in data:
            raise ValueError(f"Build-machine path in {name}")


def packages(source, out, uv, python):
    run(uv, "build", "--python", python, "--no-build-isolation", "--out-dir", out, source)
    version = project(source)
    for name in (f"noneto_cli-{version}-py3-none-any.whl", f"noneto_cli-{version}.tar.gz"):
        audit_archive(out / name)


def version_resource(version):
    number = tuple(int(part) for part in version.split(".")) + (0,)
    strings = {"CompanyName": PUBLISHER, "FileDescription": PRODUCT, "FileVersion": version + ".0",
               "InternalName": "noneto", "OriginalFilename": "noneto.exe", "ProductName": PRODUCT,
               "ProductVersion": version, "LegalCopyright": "Copyright VincentPelos"}
    entries = ",\n".join(f"StringStruct({key!r}, {value!r})" for key, value in strings.items())
    return ("VSVersionInfo(ffi=FixedFileInfo(filevers=" + repr(number) + ", prodvers=" + repr(number)
            + ", mask=0x3f, flags=0, OS=0x40004, fileType=1, subtype=0, date=(0, 0)), kids=["
            + "StringFileInfo([StringTable('040904b0', [" + entries + "])]), "
            + "VarFileInfo([VarStruct('Translation', [1033, 1200])])])\n")


def bundle(source, work, python, commit):
    version = project(source)
    resource = work / "version-info.txt"
    resource.write_text(version_resource(version), encoding="utf-8")
    env = dict(os.environ, NONETO_SOURCE=str(source), NONETO_VERSION_FILE=str(resource))
    run(python, "-m", "PyInstaller", "--noconfirm", "--clean", "--distpath", work / "dist",
        "--workpath", work / "pyinstaller", PACKAGING / "windows/noneto.spec", env=env, cwd=source)
    target = work / "dist/noneto"
    shutil.copy2(Path(sys.base_prefix) / "LICENSE.txt", target / "PYTHON-LICENSE.txt")
    shutil.copy2(PACKAGING / "windows/THIRD-PARTY.md", target / "THIRD-PARTY.md")
    for args in (["--help"], ["--lang", "es", "--help"], ["--version"], ["--self-test"]):
        run(target / "noneto.exe", *args, cwd=work)
    for provenance in target.rglob("direct_url.json"):
        provenance.unlink()
    roots = {str(source).lower(), source.as_posix().lower(), str(ROOT).lower(), ROOT.as_posix().lower()}
    for path in target.rglob("*"):
        if path.is_file():
            data = path.read_bytes().lower()
            if any(root.encode() in data or root.encode("utf-16-le") in data for root in roots):
                raise ValueError(f"Build-machine path leaked into {path.relative_to(target)}")
    info = {"version": version, "source_commit": commit, "python": sys.version.split()[0],
            "mpv": mpv_manifest(source), "signing": "unsigned"}
    (target / "BUILD-INFO.json").write_text(json.dumps(info, indent=2) + "\n", encoding="utf-8")
    return target


def compiler(work, explicit=None):
    if explicit:
        candidate = Path(explicit).resolve()
        if not candidate.is_file():
            raise ValueError("Inno Setup compiler does not exist")
        return candidate
    config = json.loads((PACKAGING / "toolchain.json").read_text(encoding="utf-8"))
    tool = work / "tools/inno/ISCC.exe"
    if not tool.exists():
        archive = work / "tools/inno-setup.exe"
        archive.parent.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(config["inno_url"], timeout=90) as response, archive.open("wb") as output:
            shutil.copyfileobj(response, output)
        if sha256(archive) != config["inno_sha256"]:
            raise ValueError("Pinned Inno Setup installer SHA-256 does not match")
        run(archive, "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CURRENTUSER", "/NOICONS",
            f"/DIR={tool.parent}")
    if not tool.is_file():
        raise ValueError("Inno Setup installation did not create ISCC.exe")
    return tool


def verify_signature(path):
    """Check only a product-owned PE; do not sign or change upstream DLLs."""
    if not (path.name == "noneto.exe" or re.fullmatch(r"Noneto-CLI-\d+\.\d+\.\d+-windows-x64-setup.exe", path.name)):
        raise ValueError("Only the Noneto application and installer are signing targets")
    script = ("$s=Get-AuthenticodeSignature -LiteralPath $env:NONETO_SIGNATURE_FILE; "
              "if ($s.Status -ne 'Valid' -or $null -eq $s.TimeStamperCertificate) { "
              "throw 'Expected a valid trusted Authenticode signature and timestamp' }")
    run("powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script,
        env=dict(os.environ, NONETO_SIGNATURE_FILE=str(path.resolve())))


def installer(source, work, out, iscc=None, test=False, signed=False):
    version = project(source)
    target = work / "dist/noneto"
    info = json.loads((target / "BUILD-INFO.json").read_text(encoding="utf-8"))
    if info["version"] != version:
        raise ValueError("Bundle and installer versions differ")
    if signed:
        verify_signature(target / "noneto.exe")
    config = {"AppVersion": version, "SourceDir": str(source), "OutputDir": str(out),
              "BundleDir": str(target), "MpvUrl": info["mpv"]["MPV_URL"], "MpvSha256": info["mpv"]["MPV_SHA256"]}
    (work / "build-config.iss").write_text("\n".join(f'#define {k} "{v}"' for k, v in config.items()) + "\n", encoding="utf-8")
    shutil.copy2(PACKAGING / "windows/noneto.iss", work / "noneto.iss")
    run(compiler(work, iscc), *(["/DTestBuild=1"] if test else []), work / "noneto.iss")


def checksums(source, out, commit, signed=False):
    version = project(source)
    names = [f"noneto_cli-{version}-py3-none-any.whl", f"noneto_cli-{version}.tar.gz",
             f"Noneto-CLI-{version}-windows-x64-setup.exe"]
    files = {name: sha256(out / name) for name in names if (out / name).is_file()}
    if not files:
        raise ValueError("No matching release artifacts exist")
    if signed:
        if names[2] not in files:
            raise ValueError("Cannot label a release signed without its installer")
        verify_signature(out / names[2])
    packages = [name for name in names[:2] if name in files]
    if packages:
        (out / "SHA256SUMS.txt").write_text("".join(f"{files[name]}  {name}\n" for name in packages), encoding="ascii")
    if names[2] in files:
        (out / "WINDOWS-SHA256SUMS.txt").write_text(f"{files[names[2]]}  {names[2]}\n", encoding="ascii")
    info = {"version": version, "source_commit": commit, "windows_signing": "signed" if signed else "unsigned",
            "files": files}
    (out / "RELEASE-MANIFEST.json").write_text(json.dumps(info, indent=2) + "\n", encoding="utf-8")
    return info


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["all", "packages", "bundle", "installer", "manifest", "verify-signature"])
    parser.add_argument("--source", type=Path, default=ROOT)
    parser.add_argument("--work", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--source-commit", default=os.environ.get("GITHUB_SHA", "local-working-tree"))
    parser.add_argument("--iscc", type=Path)
    parser.add_argument("--test-installer", action="store_true")
    parser.add_argument("--require-signed", action="store_true")
    parser.add_argument("--file", type=Path)
    args = parser.parse_args()
    source = args.source.resolve()
    version = project(source)
    work = (args.work or ROOT / "build/release" / version).resolve()
    out = (args.output or ROOT / "dist/candidate" / version).resolve()
    work.mkdir(parents=True, exist_ok=True)
    out.mkdir(parents=True, exist_ok=True)
    if args.action == "verify-signature":
        if not args.file:
            parser.error("--file is required")
        verify_signature(args.file)
        return
    if args.action in {"all", "packages", "bundle"}:
        uv, python = environment(source, work)
        if args.action in {"all", "packages"}:
            packages(source, out, uv, python)
        if args.action in {"all", "bundle"}:
            bundle(source, work, python, args.source_commit)
    if args.action in {"all", "installer"}:
        installer(source, work, out, args.iscc, args.test_installer, args.require_signed)
    if args.action != "bundle":
        # The installer is still unsigned immediately after compilation. Its outer signature
        # is checked only by the final manifest phase after the provider returns it.
        checksums(source, out, args.source_commit, args.require_signed and args.action != "installer")
    print(f"Prepared {version}: {out}\nWork directory: {work}")


if __name__ == "__main__":
    main()
