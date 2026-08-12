from __future__ import annotations

import math
from dataclasses import asdict, dataclass


GSM7_BASIC = frozenset(
    "@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞ\x1bÆæßÉ !\"#¤%&'()*+,-./0123456789:;<=>?"
    "¡ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ§¿abcdefghijklmnopqrstuvwxyzäöñüà"
)
GSM7_EXTENDED = frozenset("^{}\\[~]|€")


@dataclass(frozen=True)
class SegmentEstimate:
    encoding: str
    units: int
    segments: int
    per_segment: int
    concatenated: bool

    def as_dict(self) -> dict:
        return asdict(self)


def estimate_segments(body: str) -> SegmentEstimate:
    text = str(body or "")
    gsm_units = 0
    gsm = True
    for character in text:
        if character in GSM7_BASIC:
            gsm_units += 1
        elif character in GSM7_EXTENDED:
            gsm_units += 2
        else:
            gsm = False
            break
    if gsm:
        per_segment = 160 if gsm_units <= 160 else 153
        return SegmentEstimate(
            encoding="GSM-7",
            units=gsm_units,
            segments=max(1, math.ceil(gsm_units / per_segment)),
            per_segment=per_segment,
            concatenated=gsm_units > 160,
        )
    units = len(text.encode("utf-16-le")) // 2
    per_segment = 70 if units <= 70 else 67
    return SegmentEstimate(
        encoding="UCS-2",
        units=units,
        segments=max(1, math.ceil(units / per_segment)),
        per_segment=per_segment,
        concatenated=units > 70,
    )
