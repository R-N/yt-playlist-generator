"""Romanize CJK text to Latin (Hepburn) via pykakasi. Only non-ASCII segments are
converted — ASCII passes through byte-for-byte, so LRC timestamps, [ids], file
extensions and already-Latin words stay untouched. Ported from usb-ldac
web/api/{lyrics,metadata}.py. Japanese-accurate; Chinese falls back to Japanese
on-yomi (a pykakasi limitation, same as the reference).
"""
import re

_LRC = re.compile(r"^(\[(?:\d+:\d+(?:\.\d+)?\])+)(.*)$")
_ROMANIZER = None   # lazy — pykakasi's dict is large; only build it on first use


def _romanizer():
    global _ROMANIZER
    if _ROMANIZER is None:
        from pykakasi import kakasi
        _ROMANIZER = kakasi()
    return _ROMANIZER


def _line(text):
    if text.isascii():
        return text
    out = " ".join(item["hepburn"] for item in _romanizer().convert(text))
    return re.sub(r"\s+([、。！？…,.!?])", r"\1", out).strip()


def romanize_text(text):
    """Romanize each line, keeping any leading LRC timestamp so synced lyrics stay
    synced. A single-line string (a tag value, a filename stem) just gets romanized."""
    lines = []
    for line in (text or "").replace("\r\n", "\n").split("\n"):
        m = _LRC.match(line)
        prefix, body = m.groups() if m else ("", line)
        lines.append(prefix + _line(body))
    return "\n".join(lines)


if __name__ == "__main__":
    assert romanize_text("hello") == "hello"                      # ascii untouched
    assert romanize_text("残酷な天使のテーゼ") == "zankoku na tenshi no teeze"
    assert romanize_text("[00:12.34]夜に駆ける").startswith("[00:12.34]")  # LRC prefix kept
    print("ok")
