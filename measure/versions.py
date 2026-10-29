"""Map getClusterNodes identity rows to a client family.

``version`` alone does not identify the fork. On mainnet after Alpenglow,
Agave, JitoLabs, AgaveBam, HarmonicAgave, and Rakurai have all reported
``4.3.2``. Firedancer, HarmonicFiredancer, and FireBAM have shared calendar
versions such as ``26.10.4``. ``clientId`` is the discriminator.
"""

from __future__ import annotations

import re

from measure.constants import CLIENT_ID_FAMILY

_SEMVER = re.compile(r"\d+\.\d+\.\d+([\-+].*)?")
_CALENDAR = re.compile(r"\d{2}\.\d+\.\d+")


def family_for(client_id: str | None) -> str | None:
    """Return a family slug, or None when the node cannot be named."""
    if not client_id:
        return None
    if client_id.startswith("Unknown"):
        return None
    mapped = CLIENT_ID_FAMILY.get(client_id)
    if mapped:
        return mapped
    slug = re.sub(r"[^a-z0-9]+", "-", client_id.lower()).strip("-")
    return slug or None


def family_from_version_string(version: str) -> str:
    """Classify a bare version string. Ambiguous results stay ambiguous.

    This is documentation and a test oracle. Selection does not use it.
    """
    text = version.strip().lower()
    if "frankendancer" in text:
        return "frankendancer"
    if "firedancer" in text:
        return "firedancer"
    if "jito" in text:
        return "jito-solana"
    if "harmonic" in text or "salsa" in text:
        return "harmonic"
    if _CALENDAR.fullmatch(text):
        return "calendar-version-ambiguous"
    if _SEMVER.fullmatch(text):
        return "semver-ambiguous"
    return "other"
