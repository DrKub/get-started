"""Measurement pipeline for Solana leader transaction ordering.

Dry-run by default. Live sending requires ``--live`` and stays inside a hard
0.05 SOL fee cap. See ``measure/DESIGN_NOTES.md``.
"""

__version__ = "0.1.0"
