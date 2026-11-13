"""Quality badge in the top bar: what was requested, and what mpv actually decodes."""
from textual.widgets import Static

from ..diagnostics import emit
from ..i18n import _
from .common import clean

CSS = """
#quality { width: auto; padding: 0 1; text-style: bold; color: $primary; }
#quality.degraded { color: $warning; }
"""

# Requested tier: a ceiling, not a promise. The badge shows what mpv decodes once a track loads.
QUALITY_LABELS = {
    "LOW": ("AAC 96k", "AAC 96 kbps"),
    "HIGH": ("AAC 320k", "AAC 320 kbps"),
    "LOSSLESS": ("Lossless", "FLAC 16-bit / 44.1 kHz"),
    "HI_RES_LOSSLESS": ("Hi-Res", _("FLAC up to 24-bit / 192 kHz")),
}
TIER_RANK = {"LOW": 0, "HIGH": 1, "LOSSLESS": 2, "HI_RES_LOSSLESS": 3}
TIER_NAMES = {"HIGH": "AAC", "LOSSLESS": _("Lossless 16-bit / 44.1 kHz"), "HI_RES_LOSSLESS": "Hi-Res"}


def offered_tier(track):
    """Best tier TIDAL lists for a track, or None when it does not say."""
    tags = getattr(track, "media_metadata_tags", None) or ()
    if "HIRES_LOSSLESS" in tags:
        return "HI_RES_LOSSLESS"
    if "LOSSLESS" in tags:
        return "LOSSLESS"
    quality = getattr(track, "audio_quality", None)
    return quality if quality in TIER_NAMES else None


class QualityBadge(Static):
    def __init__(self, quality, **kwargs):
        super().__init__("◆", markup=False, **kwargs)
        self.quality = quality
        self._key = None

    def on_mount(self):
        self.show((), None)

    def show(self, audio, track):
        """What mpv decodes now; before that, only what was asked for, never as a promise.

        Getting less than the track offers (within what was asked) is flagged: that is a real loss,
        unlike a track that only exists in 16-bit / 44.1 kHz.
        """
        key = (audio, getattr(track, "id", None))
        if self._key == key:
            return
        self._key = key
        short, detail = QUALITY_LABELS.get(self.quality, (clean(self.quality), clean(self.quality)))
        asked = _("Requested: {short} ({detail}).", short=short, detail=detail)
        best = offered_tier(track)
        degraded = False
        if track is None:
            text, tip = _("Requested: {short}", short=short), f"{asked} {_("While playing, the quality that arrives is shown.")}"
        elif not audio:
            text, tip = "◆ …", f"{asked} {_("Waiting for audio.")}"
        else:
            label, received, tier = audio
            expected = min(TIER_RANK.get(self.quality, 0), TIER_RANK[best]) if best else None
            degraded = expected is not None and tier is not None and TIER_RANK[tier] < expected
            text = f"◇ {label} ↓" if degraded else f"◆ {label}"
            tip = f"{asked} {_("Received: {received}.", received=received)}"
            if best:
                tip += " " + _("This track is available in: {tier}.", tier=TIER_NAMES[best])
            if degraded:
                tip += " " + _("TIDAL delivered less than this track offers.")
                emit("playback.degraded", track_id=getattr(track, "id", None), quality=received)
        self.update(text, layout=True)
        self.tooltip = tip
        self.set_class(degraded, "degraded")
