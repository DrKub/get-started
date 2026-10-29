"""Cluster directory: vote accounts, validators.app, Alpenglow certificate.

validators.app ``software_client`` replaces the RPC ``clientId`` when a token
is set. Without a token the run keeps ``clientId`` and says so.
"""

from __future__ import annotations

import json
import urllib.request

from measure.constants import ALPENGLOW_GENESIS_CERT_SLOT, USER_AGENT
from measure.errors import RpcError


def fetch_vote_accounts(rpc) -> dict[str, dict]:
    """Map node identity to stake lamports and delinquency."""
    result = rpc.call("getVoteAccounts")
    if not isinstance(result, dict):
        raise RpcError("getVoteAccounts returned no object")
    found: dict[str, dict] = {}
    for row in result.get("current") or []:
        _add_vote(found, row, delinquent=False)
    for row in result.get("delinquent") or []:
        _add_vote(found, row, delinquent=True)
    return found


def _add_vote(found: dict[str, dict], row: dict, *, delinquent: bool) -> None:
    identity = row.get("nodePubkey")
    if not isinstance(identity, str):
        return
    stake = row.get("activatedStake")
    found[identity] = {
        "activatedStake": int(stake) if isinstance(stake, int) else 0,
        "delinquent": delinquent,
        "voteAccount": row.get("votePubkey") if isinstance(row.get("votePubkey"), str) else None,
    }


def fetch_validators_app(token: str, *, timeout_s: float = 60.0) -> list[dict]:
    request = urllib.request.Request(
        "https://www.validators.app/api/v1/validators/mainnet.json?active_only=false",
        headers={"Token": token, "User-Agent": USER_AGENT, "Accept": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=timeout_s) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, list):
        raise RpcError("validators.app returned no list")
    return payload


def stake_lamports_from_app(raw: object) -> int:
    """Normalize validators.app ``active_stake``.

    Values at or above 1e12 are lamports (a validator with >= 1000 SOL).
    Smaller values are treated as SOL, matching the API's small examples.
    """
    if not isinstance(raw, int) or isinstance(raw, bool):
        return 0
    if raw >= 1_000_000_000_000:
        return raw
    return raw * 1_000_000_000


def apply_directory(nodes: list[dict], votes: dict[str, dict], app_rows: list[dict] | None) -> str:
    """Annotate cluster nodes in place. Return the classification source."""
    app_by_account = {}
    if app_rows:
        for row in app_rows:
            account = row.get("account")
            if isinstance(account, str):
                app_by_account[account] = row
    source = "validators.app" if app_by_account else "rpc"
    for node in nodes:
        pubkey = node.get("pubkey")
        if not isinstance(pubkey, str):
            continue
        vote = votes.get(pubkey) or {}
        app = app_by_account.get(pubkey)
        if app:
            client = app.get("software_client")
            version = app.get("software_version")
            if isinstance(client, str) and client:
                node["clientId"] = client
            if isinstance(version, str) and version:
                node["version"] = version
            node["jito"] = bool(app.get("jito"))
            node["delinquent"] = bool(app.get("delinquent"))
            node["voteAccount"] = app.get("vote_account") if isinstance(app.get("vote_account"), str) else vote.get("voteAccount")
            node["activeStakeLamports"] = stake_lamports_from_app(app.get("active_stake"))
            node["classificationSource"] = "validators.app"
        else:
            node["delinquent"] = bool(vote.get("delinquent"))
            node["voteAccount"] = vote.get("voteAccount")
            node["activeStakeLamports"] = int(vote.get("activatedStake") or 0)
            node["classificationSource"] = "rpc"
            node["jito"] = None
    return source


def fetch_genesis_cert(rpc) -> dict:
    """Call ``getAgGenesisCert`` when the RPC has it. Never raises."""
    try:
        result = rpc.call("getAgGenesisCert", attempts=1)
    except RpcError as exc:
        return {"available": False, "error": str(exc), "slot": None}
    slot = None
    if isinstance(result, dict):
        block = result.get("block")
        if isinstance(block, dict) and isinstance(block.get("slot"), int):
            slot = block["slot"]
    return {
        "available": True,
        "matches_expected": slot == ALPENGLOW_GENESIS_CERT_SLOT,
        "slot": slot,
    }
