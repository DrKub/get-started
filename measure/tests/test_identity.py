"""Schedule, client identity, and memo tags."""

from __future__ import annotations

from measure.base58 import b58decode, b58encode
from measure.memo import parse_memo, render_memo
from measure.schedule import iter_windows
from measure.versions import family_for, family_from_version_string


def test_windows_split_on_rotation_and_drop_truncated_tail():
    leaders = ["A"] * 8 + ["B"] * 6 + ["C"] * 3
    full = iter_windows(leaders, 100, tail_is_complete=True)
    assert [(w.identity, w.start_slot, w.length, w.partial) for w in full] == [
        ("A", 100, 4, False),
        ("A", 104, 4, False),
        ("B", 108, 4, False),
        ("B", 112, 2, True),
        ("C", 114, 3, True),
    ]
    # Only the run that hits the end of the array is treated as cut off.
    # B's short remainder is real because C follows it. C itself is dropped.
    truncated = iter_windows(leaders, 100, tail_is_complete=False)
    assert [(w.identity, w.start_slot, w.length, w.partial) for w in truncated] == [
        ("A", 100, 4, False),
        ("A", 104, 4, False),
        ("B", 108, 4, False),
        ("B", 112, 2, True),
    ]
    tail = iter_windows(["C"] * 6, 100, tail_is_complete=False)
    assert [(w.start_slot, w.length, w.partial) for w in tail] == [(100, 4, False)]


def test_client_id_not_version_string():
    assert family_for("Agave") == "agave"
    assert family_for("JitoLabs") == "jito-solana"
    assert family_for("Firedancer") == "firedancer"
    assert family_for("HarmonicFiredancer") == "harmonic-firedancer"
    assert family_for("AgaveBam") == "agave-bam"
    assert family_for("Unknown(42)") is None
    assert family_for(None) is None
    assert family_for("ExampleClient") == "exampleclient"
    assert family_from_version_string("4.3.2") == "semver-ambiguous"
    assert family_from_version_string("26.10.4") == "calendar-version-ambiguous"
    assert family_from_version_string("2.0.13-jito") == "jito-solana"


def test_base58_roundtrip_and_memo_tag():
    from solders.pubkey import Pubkey

    pubkey = Pubkey.from_string("11111111111111111111111111111111")
    assert b58encode(bytes(pubkey)) == str(pubkey)
    assert b58decode(str(pubkey)) == bytes(pubkey)
    memo = render_memo(
        experiment_id="e1071-test",
        seq=3,
        arm="ladder",
        send_index=1,
        path="tpu",
        priority_fee_lamports=1000,
        cu_price_micro_lamports=50000,
        cu_limit=20000,
    )
    assert parse_memo(memo)["seq"] == 3
    assert parse_memo("hello") is None
    assert parse_memo(memo + "|extra") is None
