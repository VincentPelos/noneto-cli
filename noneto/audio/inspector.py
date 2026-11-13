"""Describe the audio mpv actually decodes."""
LOSSY_CODECS = {"aac", "mp3", "opus", "vorbis", "ac3", "eac3"}


def describe_audio(codec, params):
    """Label the decoded stream and rank it as a TIDAL tier (None when it maps to none).

    TIDAL FLAC is 16-bit (decoded as s16) or 24-bit (decoded as s32).
    """
    rate = params.get("samplerate") if isinstance(params, dict) else None
    if not isinstance(codec, str) or not isinstance(rate, int) or rate <= 0:
        return ()
    khz = f"{rate / 1000:g}"
    if codec == "flac":
        bits = {"s16": 16, "s16p": 16, "s32": 24, "s32p": 24}.get(params.get("format"))
        if bits:
            tier = "HI_RES_LOSSLESS" if bits > 16 or rate > 48000 else "LOSSLESS"
            return (f"FLAC {bits}/{khz}", f"FLAC {bits}-bit / {khz} kHz", tier)
        return (f"FLAC {khz}", f"FLAC / {khz} kHz", "LOSSLESS")
    name = codec.upper()
    return (f"{name} {khz}", f"{name} / {khz} kHz", "HIGH" if codec in LOSSY_CODECS else None)
