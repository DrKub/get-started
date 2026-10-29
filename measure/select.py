"""Pick a diverse upcoming-leader set."""

from __future__ import annotations

from collections import Counter, defaultdict

from measure.constants import FAMILY_PRIORITY, ROTATION_SLOTS
from measure.models import AssignedWindow, ClusterSnapshot, LeaderWindow, Target
from measure.schedule import iter_windows
from measure.versions import family_for


def _assign_window(windows: list[LeaderWindow]) -> list[tuple[LeaderWindow, str, int]]:
    """One direct-TPU window. There is no second submit path."""
    ordered = sorted(windows, key=lambda item: item.start_slot)
    if not ordered:
        return []
    return [(ordered[0], "tpu", 0)]


def _stake_lamports(node: dict) -> int:
    raw = node.get("activeStakeLamports")
    if isinstance(raw, int) and not isinstance(raw, bool):
        return max(raw, 0)
    activated = node.get("activatedStake")
    if isinstance(activated, int) and not isinstance(activated, bool):
        return max(activated, 0)
    return 0


def ensure_stake_diversity(
    picked: list[dict],
    pool: list[dict],
    *,
    min_families: int,
) -> tuple[list[dict], str | None]:
    """Keep one identity outside the ten largest stake accounts when one is scheduled."""
    ranked = sorted(pool, key=lambda item: (-item["stake_lamports"], item["identity"]))
    top_ids = {item["identity"] for item in ranked[:10]}
    if any(item["identity"] not in top_ids for item in picked):
        return picked, None
    seen: set[str] = set()
    outsiders = []
    for item in ranked:
        if item["identity"] in top_ids or item["identity"] in seen:
            continue
        seen.add(item["identity"])
        outsiders.append(item)
    if not outsiders:
        return picked, "no scheduled identity outside the ten largest by stake"
    picked_ids = {item["identity"] for item in picked}
    for outsider in outsiders:
        if outsider["identity"] in picked_ids:
            continue
        for index in range(len(picked) - 1, -1, -1):
            trial = picked[:index] + [outsider] + picked[index + 1 :]
            identities = [item["identity"] for item in trial]
            if len(identities) != len(set(identities)):
                continue
            families = {item["client_family"] for item in trial}
            if len(families) >= min_families:
                note = (
                    f"replaced {picked[index]['identity']} with {outsider['identity']} "
                    "so the set is not only the ten largest by stake"
                )
                return trial, note
    return picked, (
        "could not add an identity outside the ten largest by stake "
        "without dropping below the family minimum"
    )


def build_targets(
    snapshot: ClusterSnapshot,
    *,
    min_lead_slots: int,
    target_validators: int,
    min_client_families: int,
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
        if node.get("delinquent") is True:
            excluded["delinquent"] += 1
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
                "stake_lamports": _stake_lamports(node),
                "vote_account": node.get("voteAccount") if isinstance(node.get("voteAccount"), str) else None,
                "jito": node.get("jito") if isinstance(node.get("jito"), bool) else None,
                "classification_source": node.get("classificationSource")
                if isinstance(node.get("classificationSource"), str)
                else "rpc",
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
    pool = [item for family in grouped.values() for item in family]
    picked, stake_note = ensure_stake_diversity(picked, pool, min_families=min_client_families)
    if stake_note:
        notes.append(stake_note)
    top_ids = {
        item["identity"]
        for item in sorted(pool, key=lambda row: (-row["stake_lamports"], row["identity"]))[:10]
    }
    targets: list[Target] = []
    for candidate in picked:
        assigned_rows = _assign_window(candidate["windows"])
        if not assigned_rows:
            notes.append(f"no window left for {candidate['identity']}")
            continue
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
                vote_account=candidate["vote_account"],
                active_stake_lamports=candidate["stake_lamports"],
                jito=candidate["jito"],
                classification_source=candidate["classification_source"],
                outside_top10_stake=candidate["identity"] not in top_ids,
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
