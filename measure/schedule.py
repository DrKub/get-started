"""Group a leader array into rotation windows."""

from __future__ import annotations

from measure.constants import ROTATION_SLOTS
from measure.models import LeaderWindow


def iter_windows(
    leaders: list[str],
    start_slot: int,
    *,
    tail_is_complete: bool,
    rotation: int = ROTATION_SLOTS,
) -> list[LeaderWindow]:
    """Split consecutive leader runs into ``rotation``-slot windows.

    A run that is cut off by the lookahead is not a real rotation boundary.
    Full chunks inside that tail are kept; the leftover partial is dropped.
    When the fetched range ends on a real boundary (epoch end, or a synthetic
    schedule), a short remainder is emitted with ``partial=True``.
    """
    if rotation < 1:
        raise ValueError("rotation must be positive")
    windows: list[LeaderWindow] = []
    count = len(leaders)
    index = 0
    while index < count:
        end = index + 1
        while end < count and leaders[end] == leaders[index]:
            end += 1
        run = end - index
        truncated = end == count and not tail_is_complete
        usable = (run // rotation) * rotation if truncated else run
        offset = 0
        while offset + rotation <= usable:
            windows.append(
                LeaderWindow(
                    identity=leaders[index],
                    start_slot=start_slot + index + offset,
                    length=rotation,
                    partial=False,
                )
            )
            offset += rotation
        if not truncated and offset < usable:
            windows.append(
                LeaderWindow(
                    identity=leaders[index],
                    start_slot=start_slot + index + offset,
                    length=usable - offset,
                    partial=True,
                )
            )
        index = end
    return windows
