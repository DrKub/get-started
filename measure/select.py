"""Pick a diverse upcoming-leader set."""

from __future__ import annotations

from collections import Counter, defaultdict

from measure.constants import FAMILY_PRIORITY, ROTATION_SLOTS
from measure.models import AssignedWindow, ClusterSnapshot, LeaderWindow, Target
from measure.schedule import iter_windows
from measure.versions import family_for


def _assign_paths(
    windows: list[LeaderWindow],
    *,
    tpu_repeats: int,
    rpc_repeats: int,
) -> list[tuple[LeaderWindow, str, int]]:
    """Earliest windows, preserving a TPU-vs-RPC contrast when slots are scarce."""
    wanted = ["tpu"] * tpu_repeats + ["rpc"] * rpc_repeats
    if len(windows) >= len(wanted):
        chosen = wanted
    else:
        chosen = []
        if windows and tpu_repeats:
            chosen.append("tpu")
        if len(windows) >= 2 and rpc_repeats:
            chosen.append("rpc")
        while len(chosen) < len(windows) and chosen.count("tpu") < tpu_repeats:
            chosen.append("tpu")
        while len(chosen) < len(windows) and chosen.count("rpc") < rpc_repeats:
            chosen.append("rpc")
        chosen = chosen[: len(windows)]
    ordered = sorted(windows, key=lambda item: item.start_slot)
    return [
        (window, path, repeat_index)
        for repeat_index, (window, path) in enumerate(zip(ordered, chosen))
    ]


def build_targets(
    snapshot: ClusterSnapshot,
    *,
    min_lead_slots: int,
    target_validators: int,
    min_client_families: int,
    tpu_repeats: int,
    rpc_repeats: int,
    tail_is_complete: bool,
) -> tuple[list[Target], dict[str, int], list[str], bool, str | None]:
    excluded: Counter[str] = Counter()
    nodes: dict[str, dict] = {}
    for node in snapshot.nodes:
        pubkey = node.get("pubkey")
        if not pubkey:
            excluded["node_missing_pubkey"] += 1
            continue
        if pubkey in nodes:
            excluded["duplicate_node"] += 1
            continue
        nodes[pubkey] = node

    windows = iter_windows(
        snapshot.leaders,
        snapshot.schedule_start_slot,
        tail_is_complete=tail_is_complete,
    )
    by_identity: dict[str, list[LeaderWindow]] = defaultdict(list)
    for window in windows:
        by_identity[window.identity].append(window)

    earliest_eligible = snapshot.observed_slot + min_lead_slots
    grouped: dict[str, list] = defaultdict(list)
    for identity, identity_windows in by_identity.items():
        node = nodes.get(identity)
        if node is None:
            excluded["leader_not_in_cluster_nodes"] += 1
            continue
        client_id = node.get("clientId")
        version = node.get("version")
        tpu_quic = node.get("tpuQuic")
        family = family_for(client_id if isinstance(client_id, str) else None)
        if family is None:
            if not client_id:
                excluded["missing_client_id"] += 1
            else:
                excluded["unknown_client_id"] += 1
            continue
        if not version or not isinstance(version, str):
            excluded["missing_version"] += 1
            continue
        if not isinstance(tpu_quic, str) or ":" not in tpu_quic:
            excluded["missing_tpu_quic"] += 1
            continue
        eligible = [
            item
            for item in identity_windows
            if not item.partial
            and item.length == ROTATION_SLOTS
            and item.start_slot >= earliest_eligible
        ]
        if not eligible:
            excluded["no_eligible_window"] += 1
            continue
        feature_set = node.get("featureSet")
        grouped[family].append(
            {
                "identity": identity,
                "client_id": client_id,
                "client_family": family,
                "version": version,
                "feature_set": feature_set if isinstance(feature_set, int) else None,
                "tpu_quic": tpu_quic,
                "gossip": node.get("gossip") if isinstance(node.get("gossip"), str) else None,
                "windows": sorted(eligible, key=lambda item: item.start_slot),
            }
        )

    for family in grouped:
        grouped[family].sort(
            key=lambda item: (
                -len(item["windows"]),
                item["windows"][0].start_slot,
                item["identity"],
            )
        )

    family_order = [family for family in FAMILY_PRIORITY if family in grouped]
    family_order.extend(sorted(set(grouped) - set(family_order)))

    picked: list[dict] = []
    seen: set[str] = set()
    while len(picked) < target_validators:
        progressed = False
        for family in family_order:
            for candidate in grouped[family]:
                if candidate["identity"] in seen:
                    continue
                picked.append(candidate)
                seen.add(candidate["identity"])
                progressed = True
                break
            if len(picked) >= target_validators:
                break
        if not progressed:
            break

    notes: list[str] = []
    targets: list[Target] = []
    requested = ["tpu"] * tpu_repeats + ["rpc"] * rpc_repeats
    for candidate in picked:
        assigned_rows = _assign_paths(
            candidate["windows"],
            tpu_repeats=tpu_repeats,
            rpc_repeats=rpc_repeats,
        )
        assigned = [
            AssignedWindow(
                start_slot=window.start_slot,
                length=window.length,
                path=path,
                repeat_index=repeat_index,
                partial=window.partial,
            )
            for window, path, repeat_index in assigned_rows
        ]
        got = [item.path for item in assigned]
        if got != requested[: len(got)] or len(got) != len(requested):
            notes.append(
                f"shortfall: {candidate['identity']} ({candidate['client_family']} "
                f"{candidate['version']}) wanted {requested}, assigned {got} "
                f"from {len(candidate['windows'])} eligible windows"
            )
        targets.append(
            Target(
                identity=candidate["identity"],
                client_id=candidate["client_id"],
                client_family=candidate["client_family"],
                version=candidate["version"],
                feature_set=candidate["feature_set"],
                tpu_quic=candidate["tpu_quic"],
                gossip=candidate["gossip"],
                windows_available=len(candidate["windows"]),
                assigned=assigned,
            )
        )

    families = {target.client_family for target in targets}
    reason = None
    if len(targets) < target_validators:
        reason = (
            f"only {len(targets)} validators with an eligible leader window "
            f"(need {target_validators})"
        )
    elif len(families) < min_client_families:
        reason = (
            f"only {len(families)} client families in the target set "
            f"(need {min_client_families})"
        )
    ok = reason is None
    return targets, dict(excluded), notes, ok, reason
