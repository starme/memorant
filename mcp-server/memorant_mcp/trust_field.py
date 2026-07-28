"""Trust-field modality + freshness tide (spectrum × time)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal

from .config import MemorantSettings, PersonaBehavior, load_settings
from .memory_schema import LifecycleState, TrustTier

Modality = Literal["usable", "suspicious", "negative", "dusty"]
Tide = Literal["hot", "aging", "dusty"]

# Days since last heat signal → aging / dusty (by freshness dim).
_TIDE_DAYS: dict[str, tuple[float, float]] = {
    "low": (60.0, 180.0),
    "mid": (30.0, 90.0),
    "high": (14.0, 45.0),
}

# Minimum token overlap for negative-trust near-pit injection.
_NEGATIVE_OVERLAP: dict[str, int] = {
    "low": 3,
    "mid": 2,
    "high": 1,
}


@dataclass(frozen=True)
class TrustView:
    modality: Modality
    tide: Tide
    label: str
    agent_rail: str
    human_hint: str | None
    channel: Literal["agent", "human", "activity"]


def _parse_time(value: Any) -> float:
    if value is None:
        return 0.0
    if isinstance(value, datetime):
        dt = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def heat_age_days(memory: dict[str, Any], *, now: datetime | None = None) -> float:
    """Days since last heat (recall / update). Larger = colder.

    Prefer recall/update over created_at so a newly ingested old memory can still
    be dusty until it earns heat.
    """
    now_ts = (now or datetime.now(timezone.utc)).timestamp()
    heat = max(
        _parse_time(memory.get("last_recalled_at")),
        _parse_time(memory.get("updated_at")),
    )
    if heat <= 0:
        heat = _parse_time(memory.get("created_at"))
    if heat <= 0:
        return 10_000.0
    return max(0.0, (now_ts - heat) / 86400.0)


def classify_tide(
    memory: dict[str, Any],
    *,
    freshness: str = "mid",
    now: datetime | None = None,
) -> Tide:
    aging_days, dusty_days = _TIDE_DAYS.get(freshness, _TIDE_DAYS["mid"])
    age = heat_age_days(memory, now=now)
    if age >= dusty_days:
        return "dusty"
    if age >= aging_days:
        return "aging"
    return "hot"


def is_negative_lifecycle(memory: dict[str, Any]) -> bool:
    lifecycle = memory.get("lifecycle")
    return lifecycle in {
        LifecycleState.corrected.value,
        LifecycleState.rejected.value,
    }


def negative_overlap_threshold(guardrail: str = "high") -> int:
    return _NEGATIVE_OVERLAP.get(guardrail, _NEGATIVE_OVERLAP["high"])


def classify_modality(
    memory: dict[str, Any],
    *,
    tide: Tide | None = None,
    behavior: PersonaBehavior | None = None,
    now: datetime | None = None,
) -> Modality:
    b = behavior or load_settings().behavior
    if is_negative_lifecycle(memory):
        return "negative"
    tide = tide or classify_tide(memory, freshness=b.freshness, now=now)
    trust = memory.get("trust_tier")
    if trust == TrustTier.provisional.value and not memory.get("legacy"):
        return "suspicious"
    if tide == "dusty":
        return "dusty"
    return "usable"


def build_trust_view(
    memory: dict[str, Any],
    *,
    settings: MemorantSettings | None = None,
    now: datetime | None = None,
    overlap: int = 0,
) -> TrustView:
    cfg = settings or load_settings()
    b = cfg.behavior
    tide = classify_tide(memory, freshness=b.freshness, now=now)
    modality = classify_modality(memory, tide=tide, behavior=b, now=now)
    title = str(memory.get("title") or "memory")
    claim = str(memory.get("claim") or "").strip()

    if modality == "negative":
        label = "[NEGATIVE-TRUST·勿用]"
        agent_rail = (
            f"DENY: do not apply '{title}'. "
            f"Formerly wrong claim context: {claim[:240]}. "
            "If a replacement memory exists, prefer that; else refute only."
        )
        human_hint = None
        if b.voice != "low" and overlap >= negative_overlap_threshold(b.guardrail):
            human_hint = (
                f"近坑：曾误用「{title}」——本次仍避开？"
                if cfg.tone != "playful"
                else f"哎，这个坑（{title}）以前踩过——这次绕开？"
            )
            if cfg.tone == "serious":
                human_hint = f"负信任命中「{title}」。是否仍按勿用处理？"
        channel: Literal["agent", "human", "activity"] = (
            "human" if human_hint else "agent"
        )
        return TrustView(
            modality=modality,
            tide=tide,
            label=label,
            agent_rail=agent_rail,
            human_hint=human_hint,
            channel=channel,
        )

    if modality == "suspicious":
        label = "[PROVISIONAL—待验证]"
        agent_rail = (
            f"HOLD: '{title}' is provisional — do not treat as settled truth. "
            f"Skeleton only: {claim[:200]}"
        )
        human_hint = None
        if b.voice == "high":
            human_hint = f"可疑记忆「{title}」：仍用 / 核实 / 忽略？"
        return TrustView(
            modality=modality,
            tide=tide,
            label=label,
            agent_rail=agent_rail,
            human_hint=human_hint,
            channel="human" if human_hint else "agent",
        )

    if modality == "dusty":
        label = "[DUSTY—久未验证]"
        agent_rail = (
            f"VERIFY-FIRST: '{title}' is deep-dusty — index/line-draft only; "
            f"do NOT inject full procedural steps as truth. Claim: {claim[:180]}"
        )
        human_hint = None
        if b.voice != "low":
            human_hint = f"「{title}」已久未验证——续保仍敢用，还是先核实？"
        return TrustView(
            modality=modality,
            tide=tide,
            label=label,
            agent_rail=agent_rail,
            human_hint=human_hint,
            channel="human" if human_hint else "agent",
        )

    # usable (+ optional aging humility)
    if memory.get("legacy"):
        label = "[VERIFIED·legacy]"
    else:
        label = "[VERIFIED]"
    if tide == "aging":
        label = "[VERIFIED·aging]"
        agent_rail = (
            f"USE-WITH-CARE: '{title}' still positive-trust but aging. "
            f"Skeleton: {claim[:220]}"
        )
    else:
        agent_rail = f"USE: '{title}'. Skeleton: {claim[:240]}"
    return TrustView(
        modality="usable",
        tide=tide,
        label=label,
        agent_rail=agent_rail,
        human_hint=None,
        channel="agent",
    )
