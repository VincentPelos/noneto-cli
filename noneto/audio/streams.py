"""Resolve a track without parsing DASH timelines into unbounded segment lists."""
import json
from pathlib import Path
import tempfile
from urllib.parse import urljoin

from defusedxml import ElementTree
from defusedxml.common import DefusedXmlException

from ..errors import AppError
from ..i18n import _
from ..security import https_url
from ..diagnostics import emit, track_id

MAX_MANIFEST_BYTES = 2 * 1024 * 1024
DASH_NS = "urn:mpeg:dash:schema:mpd:2011"
TIERS = {"LOW", "HIGH", "LOSSLESS", "HI_RES", "HI_RES_LOSSLESS"}


def validate_dash(data):
    try:
        root = ElementTree.fromstring(data, forbid_dtd=True)
    except (DefusedXmlException, ElementTree.ParseError, ValueError):
        raise AppError(_("Invalid DASH manifest.")) from None
    if root.tag != f"{{{DASH_NS}}}MPD":
        raise AppError(_("Invalid DASH manifest."))
    references = 0

    def visit(node, base=None, depth=0):
        nonlocal references
        if depth > 32:
            raise AppError(_("DASH manifest is nested too deeply."))
        if not isinstance(node.tag, str) or not node.tag.startswith(f"{{{DASH_NS}}}"):
            raise AppError(_("Unsupported DASH namespace."))
        tag = node.tag.rsplit("}", 1)[-1]
        if tag in {"ContentProtection", "Location", "PatchLocation", "UTCTiming"}:
            raise AppError(_("The manifest uses an unsupported DASH feature."))
        if any(key.startswith("{http://www.w3.org/1999/xlink}")
               or key == "{http://www.w3.org/XML/1998/namespace}base" for key in node.attrib):
            raise AppError(_("External XML references are not allowed."))
        bases = node.findall(f"{{{DASH_NS}}}BaseURL")
        if len(bases) > 1:
            raise AppError(_("Multiple BaseURL elements are not supported."))
        if bases:
            value = (bases[0].text or "").strip()
            base = https_url(urljoin(base or "", value))
            references += 1
        for key in ("media", "initialization", "sourceURL", "index"):
            if key in node.attrib:
                value = node.attrib[key]
                if "\\" in value or any(ord(c) < 33 for c in value):
                    raise AppError(_("Invalid DASH reference."))
                https_url(urljoin(base or "", value))
                references += 1
        for child in node:
            if child.tag != f"{{{DASH_NS}}}BaseURL":
                visit(child, base, depth + 1)

    visit(root)
    if not references:
        raise AppError(_("The manifest has no audio destinations."))


def stream_target(track, directory):
    emit("stream.requested", track_id=track_id(track), stage="tidal_api")
    stream = track.get_stream()
    mime = stream.manifest_mime_type
    tier = getattr(stream, "audio_quality", None)  # what TIDAL says it delivers, not what was asked
    emit("stream.received", track_id=track_id(track),
         manifest_type=mime if mime in {"application/dash+xml", "application/vnd.tidal.bts"} else "unknown",
         quality=tier if tier in TIERS else "unknown")
    # Check before decoding the base64 envelope in tidalapi.
    encoded = getattr(stream, "manifest", "")
    if isinstance(encoded, str) and len(encoded) > MAX_MANIFEST_BYTES * 4 // 3 + 4:
        raise AppError(_("Audio manifest too large."))
    data = stream.get_manifest_data()
    if not isinstance(data, str) or len(data.encode("utf-8")) > MAX_MANIFEST_BYTES:
        raise AppError(_("Audio manifest too large."))
    if stream.manifest_mime_type == "application/dash+xml":
        validate_dash(data)
        emit("stream.validated", track_id=track_id(track), target_kind="dash")
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", suffix=".mpd",
                                         dir=directory, delete=False) as target:
            target.write(data)
            return str(Path(target.name).resolve())
    if stream.manifest_mime_type != "application/vnd.tidal.bts":
        raise AppError(_("Unsupported manifest format."))
    try:
        manifest = json.loads(data)
        if not isinstance(manifest, dict):
            raise ValueError
        if manifest.get("encryptionType") not in (None, "NONE"):
            raise AppError(_("This stream needs an unsupported playback method."))
        urls = manifest["urls"]
        if not isinstance(urls, list) or not urls:
            raise ValueError
        target = https_url(urls[0])
        emit("stream.validated", track_id=track_id(track), target_kind="https")
        return target
    except (ValueError, KeyError, TypeError):
        raise AppError(_("TIDAL did not return a valid audio manifest.")) from None
