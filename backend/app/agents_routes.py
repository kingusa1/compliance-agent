"""Agent leaderboard for DEMO-07 + L4 drilldown.

Aggregates call metrics by agent_name. No identity model behind this
yet — the agent is just a string captured during transcription. Good
enough for demo: show who's making the calls + how many fail.

Escalation = computed: any agent with ≥2 non-compliant calls in the
last 30 days OR with an open fix-directive on any of their calls.

L4 additions:
  • GET /api/agents/{name}/drilldown — returns critical_count_7d,
    pass_rate_30d, open_directives, open_rejections_value_gbp, the
    LIST of dead_rejections (audit Fix #16) with dead_reason text, and
    retraining_assigned + retraining_reason (audit Fix #22).
  • PATCH /api/agents/{name} — sets retraining_assigned + reason.

Both endpoints degrade gracefully when the L4-introduced columns aren't
in the schema yet (older test DBs / pre-migration deployments): missing
columns surface as defaults rather than 500s.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from app._clock import utcnow
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, inspect, text
from sqlalchemy.exc import OperationalError, ProgrammingError
from sqlalchemy.orm import Session

from app.database import get_db
from app.logger import log
from app.models import Call, FixDirective, SalesAgentAlias
from app.reviewers import current_reviewer, require_lead


agents_router = APIRouter(prefix="/api/agents", tags=["agents"])


def _load_agent_aliases(db: Session) -> dict[str, str]:
    """W1 (v3-watt-coverage): build {alias.lower() → canonical_name}.

    Best-effort — if the table is missing (older DBs / pre-migration test
    envs) we return an empty map and the route falls through to raw
    agent_name strings unchanged.
    """
    try:
        rows = db.query(SalesAgentAlias).all()
    except Exception:
        return {}
    return {r.alias.strip().lower(): r.canonical_name for r in rows if r.alias}


def _canonicalize_agent(name: str | None, aliases: dict[str, str]) -> str | None:
    """Map a raw agent string to its canonical form via the alias table.

    Falls back to the original string when no mapping is registered (the
    Settings tab in W4 will let admins backfill aliases over time).
    """
    if not name:
        return name
    return aliases.get(name.strip().lower(), name)


@agents_router.get("")
def list_agents(
    db: Session = Depends(get_db),
    _user: dict = Depends(current_reviewer),  # 2026-05-24 C5: agent PII gated
) -> dict:
    cutoff = utcnow() - timedelta(days=30)
    # W1 (v3-watt-coverage): load alias table once; canonicalise raw names
    # before grouping. Best-effort — see _canonicalize_agent.
    aliases = _load_agent_aliases(db)
    rows = (
        db.query(
            Call.agent_name,
            func.count(Call.id).label("total"),
            func.count().filter(Call.compliant.is_(True)).label("compliant"),
            func.count().filter(Call.compliant.is_(False)).label("non_compliant"),
            func.max(Call.created_at).label("last_call_at"),
        )
        .filter(Call.agent_name.isnot(None))
        .group_by(Call.agent_name)
        .all()
    )

    # Open directives per agent (joined via call.agent_name).
    open_directives_rows = (
        db.query(Call.agent_name, func.count(FixDirective.id).label("open"))
        .join(FixDirective, FixDirective.call_id == Call.id)
        .filter(FixDirective.status.in_(("pending", "in_progress")))
        .group_by(Call.agent_name)
        .all()
    )
    open_by_agent: dict[str, int] = {}
    for r in open_directives_rows:
        if not r.agent_name:
            continue
        canon = _canonicalize_agent(r.agent_name, aliases) or r.agent_name
        open_by_agent[canon] = open_by_agent.get(canon, 0) + r.open

    # Recent non-compliant counts (last 30d) for escalation flag.
    recent_fail_rows = (
        db.query(Call.agent_name, func.count(Call.id).label("recent_fail"))
        .filter(
            Call.agent_name.isnot(None),
            Call.compliant.is_(False),
            Call.created_at >= cutoff,
        )
        .group_by(Call.agent_name)
        .all()
    )
    recent_fail_by_agent: dict[str, int] = {}
    for r in recent_fail_rows:
        if not r.agent_name:
            continue
        canon = _canonicalize_agent(r.agent_name, aliases) or r.agent_name
        recent_fail_by_agent[canon] = recent_fail_by_agent.get(canon, 0) + r.recent_fail

    # W1: aggregate raw rows by canonical name. Aliases not yet registered
    # pass through as their raw string — best-effort grouping.
    grouped: dict[str, dict[str, Any]] = {}
    for r in rows:
        canon = _canonicalize_agent(r.agent_name, aliases) or r.agent_name
        bucket = grouped.setdefault(canon, {
            "agent_name": canon,
            "total": 0,
            "compliant": 0,
            "non_compliant": 0,
            "last_call_at": None,
        })
        bucket["total"] += r.total
        bucket["compliant"] += r.compliant
        bucket["non_compliant"] += r.non_compliant
        if r.last_call_at and (
            bucket["last_call_at"] is None or r.last_call_at > bucket["last_call_at"]
        ):
            bucket["last_call_at"] = r.last_call_at

    agents = []
    for canon, b in grouped.items():
        recent_fail = recent_fail_by_agent.get(canon, 0)
        open_n = open_by_agent.get(canon, 0)
        # Escalation when the agent has stacked failures or open
        # follow-ups — flags reviewer attention.
        needs_escalation = recent_fail >= 2 or open_n > 0
        agents.append({
            "agent_name": canon,
            "total_calls": b["total"],
            "compliant": b["compliant"],
            "non_compliant": b["non_compliant"],
            "recent_non_compliant_30d": recent_fail,
            "open_directives": open_n,
            "last_call_at": b["last_call_at"].isoformat() if b["last_call_at"] else None,
            "needs_escalation": needs_escalation,
        })
    agents.sort(key=lambda a: (-a["needs_escalation"], -a["non_compliant"], a["agent_name"]))
    return {"agents": agents}


# ── L4: drilldown + retraining management ───────────────────────────────


def _has_column(db: Session, table: str, column: str) -> bool:
    """Best-effort column probe so the route can degrade gracefully when
    the L4 migration hasn't run yet (or in older test SQLite DBs). Returns
    False on any inspection error rather than re-raising — the route will
    surface defaults instead of 500ing the page.
    """
    try:
        cols = inspect(db.get_bind()).get_columns(table)
        return any(c["name"] == column for c in cols)
    except Exception:
        return False


def _safe_float(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


@agents_router.get("/{agent_name}/drilldown")
def agent_drilldown(
    agent_name: str,
    db: Session = Depends(get_db),
    _user: dict = Depends(current_reviewer),  # 2026-05-24 C5
) -> dict:
    now = utcnow()
    cutoff_7 = now - timedelta(days=7)
    cutoff_30 = now - timedelta(days=30)

    # Critical flag count over the last 7 days. The `flags` table may not
    # exist in older DBs — degrade to 0 if so.
    critical_count_7d = 0
    try:
        row = db.execute(
            text(
                """
                SELECT COUNT(*) AS n
                FROM flags f
                JOIN calls c ON c.id = f.call_id
                WHERE c.agent_name = :agent
                  AND f.severity = 'critical'
                  AND f.created_at >= :cutoff
                """
            ),
            {"agent": agent_name, "cutoff": cutoff_7},
        ).fetchone()
        critical_count_7d = int(row.n) if row else 0
    except (OperationalError, ProgrammingError):
        critical_count_7d = 0

    # Pass rate over last 30d.
    rate_row = (
        db.query(
            func.count(Call.id).label("total"),
            func.count().filter(Call.compliant.is_(True)).label("ok"),
        )
        .filter(Call.agent_name == agent_name, Call.created_at >= cutoff_30)
        .one()
    )
    pass_rate_30d = (rate_row.ok / rate_row.total) if rate_row.total else None

    # Open directives count (pending|in_progress).
    open_dir_count = (
        db.query(func.count(FixDirective.id))
        .join(Call, Call.id == FixDirective.call_id)
        .filter(
            Call.agent_name == agent_name,
            FixDirective.status.in_(("pending", "in_progress")),
        )
        .scalar()
        or 0
    )

    # Open £ at risk: sum of deal_value_gbp on customer_deals reachable via
    # this agent's calls where there's still an open directive. Tolerate
    # missing customer_deals or fix_directives joins.
    open_value = None
    try:
        row = db.execute(
            text(
                """
                SELECT COALESCE(SUM(d.deal_value_gbp), 0) AS v
                FROM fix_directives fd
                JOIN calls c ON c.id = fd.call_id
                LEFT JOIN customer_deals d ON d.id = c.deal_id
                WHERE c.agent_name = :agent
                  AND fd.status IN ('pending', 'in_progress')
                """
            ),
            {"agent": agent_name},
        ).fetchone()
        open_value = _safe_float(row.v) if row else None
    except (OperationalError, ProgrammingError):
        open_value = None

    # Dead rejections LIST (audit Fix #16). dead_reason / status_dead_reason
    # column may not exist yet — fall back to parsing the directive body.
    dead_rejections: list[dict[str, Any]] = []
    has_dead_reason_col = _has_column(db, "fix_directives", "dead_reason")
    try:
        sql = """
            SELECT
                fd.id::text                AS directive_id,
                d.id::text                 AS deal_id,
                COALESCE(d.customer_name, c.customer_name) AS customer_name,
                {dead_col}                 AS dead_reason,
                COALESCE(fd.fixed_at, fd.updated_at, fd.created_at) AS rejected_at,
                fd.body                    AS body
            FROM fix_directives fd
            JOIN calls c              ON c.id = fd.call_id
            LEFT JOIN customer_deals d ON d.id = c.deal_id
            WHERE c.agent_name = :agent
              AND fd.status = 'dead'
            ORDER BY rejected_at DESC NULLS LAST
            LIMIT 100
        """.format(dead_col=("fd.dead_reason" if has_dead_reason_col else "NULL"))
        rows = db.execute(text(sql), {"agent": agent_name}).fetchall()
        for r in rows:
            dr = r.dead_reason
            if not dr and r.body:
                # Body is structured "key=value\n…" — pluck dead_reason= when present.
                m = re.search(r"^dead_reason=(.+)$", r.body, re.MULTILINE)
                dr = m.group(1).strip() if m else None
            dead_rejections.append(
                {
                    "deal_id": r.deal_id or r.directive_id,
                    "customer_name": r.customer_name,
                    "dead_reason": dr,
                    "rejected_at": r.rejected_at.isoformat() if r.rejected_at else None,
                }
            )
    except (OperationalError, ProgrammingError) as e:
        log.warning(f"agent dead-rejections query degraded: {e}")
        dead_rejections = []

    # Retraining columns live on `profiles` (per L8 migration
    # d4e5f6a7b8c9). Match by name — agents are referenced by string
    # names on calls, and profiles.name carries the same display name.
    # When the columns are absent (older test DBs) report defaults.
    retraining_assigned = False
    retraining_reason: str | None = None
    if _has_column(db, "profiles", "retraining_assigned"):
        try:
            r = db.execute(
                text(
                    "SELECT retraining_assigned, retraining_reason "
                    "FROM profiles WHERE name = :agent ORDER BY created_at ASC LIMIT 1"
                ),
                {"agent": agent_name},
            ).fetchone()
            if r is not None:
                retraining_assigned = bool(r.retraining_assigned)
                retraining_reason = r.retraining_reason
        except (OperationalError, ProgrammingError):
            pass

    # Recent calls for this agent — surfaced in the drilldown so the page
    # has SOMETHING to show even when dead_rejections is empty (the previous
    # behavior was a blank page on every agent without dead-flag history).
    from app.models import Call as _Call
    recent_call_rows = (
        db.query(
            _Call.id, _Call.filename, _Call.customer_name,
            _Call.detected_supplier, _Call.score, _Call.compliant,
            _Call.compliance_status, _Call.created_at, _Call.completed_at,
            _Call.reason, _Call.duration_seconds,
        )
        .filter(_Call.agent_name == agent_name)
        .order_by(_Call.created_at.desc())
        .limit(20)
        .all()
    )
    recent_calls = [
        {
            "id": r.id,
            "filename": r.filename,
            "customer_name": r.customer_name,
            "detected_supplier": r.detected_supplier,
            "score": r.score,
            "compliant": r.compliant,
            "compliance_status": r.compliance_status,
            "created_at": (r.created_at.isoformat() if r.created_at else None),
            "completed_at": (r.completed_at.isoformat() if r.completed_at else None),
            "reason": r.reason,
            "duration_seconds": (
                float(r.duration_seconds) if r.duration_seconds is not None else None
            ),
        }
        for r in recent_call_rows
    ]

    # ── 2026-05-27 — Quality-reviewer enrichment ─────────────────────
    #
    # Owner mandate: "make the agent page much more better and attractive
    # and have all the information that the quality person will need to
    # take a decision". Add: total_calls lifetime, avg_score_30d,
    # severity_breakdown (last 30d), top_failed_checkpoints (last 30d),
    # supplier_mix (last 30d), call_type_mix (last 30d), qc_flag_count
    # (QualityCheckerAgent verdict=block last 30d), weekly_trend (8-week
    # pass-rate sparkline series), best_call_id + worst_call_id (best vs
    # worst score in the recent calls window).
    #
    # All gracefully degrade to defaults on schema mismatches so the
    # endpoint never 500s on older DBs.

    total_calls_lifetime = (
        db.query(func.count(Call.id))
        .filter(Call.agent_name == agent_name)
        .scalar()
        or 0
    )

    # Avg score over the last 30d. Parse "X/Y" → ratio in Python to keep
    # the SQL dialect-portable.
    avg_score_30d: float | None = None
    score_rows = (
        db.query(Call.score)
        .filter(
            Call.agent_name == agent_name,
            Call.created_at >= cutoff_30,
            Call.score.isnot(None),
        )
        .all()
    )
    ratios: list[float] = []
    for (s,) in score_rows:
        try:
            num, den = (s or "").split("/", 1)
            denf = float(den)
            if denf > 0:
                ratios.append(float(num) / denf)
        except (ValueError, AttributeError):
            continue
    if ratios:
        avg_score_30d = round(sum(ratios) / len(ratios), 3)

    # Severity breakdown (last 30d). `flags` table may be missing on
    # older DBs — fall through to a checkpoint-results parse on Call.
    severity_breakdown: dict[str, int] = {"critical": 0, "high": 0, "medium": 0, "low": 0}
    try:
        sev_rows = db.execute(
            text(
                """
                SELECT f.severity, COUNT(*) AS n
                  FROM flags f
                  JOIN calls c ON c.id = f.call_id
                 WHERE c.agent_name = :agent
                   AND f.created_at >= :cutoff
                 GROUP BY f.severity
                """
            ),
            {"agent": agent_name, "cutoff": cutoff_30},
        ).fetchall()
        for r in sev_rows:
            key = (r.severity or "medium").lower()
            if key in severity_breakdown:
                severity_breakdown[key] = int(r.n)
    except (OperationalError, ProgrammingError):
        pass

    # Top failed checkpoints (last 30d). Parses each call's
    # checkpoint_results JSON in Python — cheap for ~100 calls/agent;
    # surface the 5 most-frequent failed `name` strings.
    top_failed_checkpoints: list[dict[str, Any]] = []
    try:
        from collections import Counter
        cp_rows = (
            db.query(Call.checkpoint_results)
            .filter(
                Call.agent_name == agent_name,
                Call.created_at >= cutoff_30,
                Call.checkpoint_results.isnot(None),
            )
            .all()
        )
        counter: Counter[str] = Counter()
        for (raw,) in cp_rows:
            try:
                arr = __import__("json").loads(raw or "[]")
            except (TypeError, ValueError):
                continue
            if not isinstance(arr, list):
                continue
            for cp in arr:
                if not isinstance(cp, dict):
                    continue
                # Effective verdict: reviewer override wins, else AI status.
                eff = cp.get("reviewer_verdict") or cp.get("status")
                if eff in ("fail", "unverified", "partial"):
                    nm = (cp.get("name") or "").strip()
                    if nm:
                        counter[nm] += 1
        top_failed_checkpoints = [
            {"name": n, "count": c} for n, c in counter.most_common(5)
        ]
    except Exception as e:  # noqa: BLE001 — never block the response
        log.warning(f"top_failed_checkpoints failed (non-fatal): {e}")

    # Supplier mix + call_type mix (last 30d) — single GROUP BY each.
    supplier_mix: dict[str, int] = {}
    call_type_mix: dict[str, int] = {}
    try:
        for r in db.execute(
            text(
                """
                SELECT COALESCE(detected_supplier, 'Unknown') AS s, COUNT(*) AS n
                  FROM calls
                 WHERE agent_name = :agent AND created_at >= :cutoff
                 GROUP BY s
                """
            ),
            {"agent": agent_name, "cutoff": cutoff_30},
        ).fetchall():
            supplier_mix[r.s] = int(r.n)
        for r in db.execute(
            text(
                """
                SELECT COALESCE(call_type, 'unset') AS t, COUNT(*) AS n
                  FROM calls
                 WHERE agent_name = :agent AND created_at >= :cutoff
                 GROUP BY t
                """
            ),
            {"agent": agent_name, "cutoff": cutoff_30},
        ).fetchall():
            call_type_mix[r.t] = int(r.n)
    except (OperationalError, ProgrammingError):
        pass

    # QualityCheckerAgent verdict=block count (last 30d). Column may be
    # NULL on older calls — narrow predicate keeps the scan partial-
    # indexed via ix_calls_quality_check_verdict.
    qc_block_count_30d = 0
    try:
        row = db.execute(
            text(
                """
                SELECT COUNT(*) AS n
                  FROM calls
                 WHERE agent_name = :agent
                   AND created_at >= :cutoff
                   AND (quality_check->>'verdict') = 'block'
                """
            ),
            {"agent": agent_name, "cutoff": cutoff_30},
        ).fetchone()
        qc_block_count_30d = int(row.n) if row else 0
    except (OperationalError, ProgrammingError):
        qc_block_count_30d = 0

    # 8-week pass-rate sparkline: one entry per week, oldest first.
    weekly_trend: list[dict[str, Any]] = []
    try:
        rows_week = db.execute(
            text(
                """
                WITH weeks AS (
                  SELECT generate_series(0, 7) AS i
                )
                SELECT
                  (NOW() AT TIME ZONE 'utc')::date - (w.i * 7) AS week_end,
                  (NOW() AT TIME ZONE 'utc')::date - ((w.i + 1) * 7) AS week_start,
                  (
                    SELECT COUNT(*)::int FROM calls c
                     WHERE c.agent_name = :agent
                       AND c.created_at >= (NOW() AT TIME ZONE 'utc')::date - ((w.i + 1) * 7)
                       AND c.created_at <  (NOW() AT TIME ZONE 'utc')::date - (w.i * 7)
                  ) AS total,
                  (
                    SELECT COUNT(*)::int FROM calls c
                     WHERE c.agent_name = :agent
                       AND c.compliant IS TRUE
                       AND c.created_at >= (NOW() AT TIME ZONE 'utc')::date - ((w.i + 1) * 7)
                       AND c.created_at <  (NOW() AT TIME ZONE 'utc')::date - (w.i * 7)
                  ) AS ok
                FROM weeks w
                ORDER BY w.i DESC
                """
            ),
            {"agent": agent_name},
        ).fetchall()
        weekly_trend = [
            {
                "week_start": r.week_start.isoformat() if r.week_start else None,
                "week_end": r.week_end.isoformat() if r.week_end else None,
                "total": int(r.total),
                "ok": int(r.ok),
                "pass_rate": (float(r.ok) / float(r.total)) if r.total else None,
            }
            for r in rows_week
        ]
    except (OperationalError, ProgrammingError):
        weekly_trend = []

    # Best + worst recent calls — picked from the already-loaded
    # recent_calls list so this costs zero extra DB time.
    best_call_id: str | None = None
    worst_call_id: str | None = None
    if recent_calls:
        def _ratio(c: dict) -> float:
            s = (c.get("score") or "")
            try:
                num, den = s.split("/", 1)
                d = float(den)
                return float(num) / d if d > 0 else -1.0
            except (ValueError, AttributeError):
                return -1.0
        scored = [c for c in recent_calls if _ratio(c) >= 0]
        if scored:
            best_call_id = max(scored, key=_ratio).get("id")
            worst_call_id = min(scored, key=_ratio).get("id")

    return {
        "agent_name": agent_name,
        "critical_count_7d": critical_count_7d,
        "pass_rate_30d": pass_rate_30d,
        "open_directives": int(open_dir_count),
        "open_rejections_value_gbp": open_value,
        "retraining_assigned": retraining_assigned,
        "retraining_reason": retraining_reason,
        "dead_rejections": dead_rejections,
        "recent_calls": recent_calls,
        # 2026-05-27 enrichment for the quality-reviewer page redesign.
        "total_calls_lifetime": int(total_calls_lifetime),
        "avg_score_30d": avg_score_30d,
        "severity_breakdown_30d": severity_breakdown,
        "top_failed_checkpoints_30d": top_failed_checkpoints,
        "supplier_mix_30d": supplier_mix,
        "call_type_mix_30d": call_type_mix,
        "qc_block_count_30d": qc_block_count_30d,
        "weekly_trend": weekly_trend,
        "best_call_id": best_call_id,
        "worst_call_id": worst_call_id,
    }


class AgentRetrainingPatch(BaseModel):
    retraining_assigned: bool
    retraining_reason: str | None = Field(default=None, max_length=2000)


@agents_router.patch("/{agent_name}")
def patch_agent(
    agent_name: str,
    payload: AgentRetrainingPatch,
    db: Session = Depends(get_db),
    _user: dict = Depends(require_lead),  # 2026-05-24 C5: mutation = lead+
) -> dict:
    if not _has_column(db, "profiles", "retraining_assigned"):
        # Column not yet shipped — surface 422 so the UI can show a helpful
        # message instead of pretending the patch persisted.
        raise HTTPException(422, "profiles.retraining_assigned column not present yet")

    # Update profiles row matched by display name. We do not auto-create
    # a profile here — agent identity in v2 is still string-based on
    # calls.agent_name; not every call agent has a profile yet.
    result = db.execute(
        text(
            """
            UPDATE profiles
               SET retraining_assigned = :assigned,
                   retraining_reason   = :reason
             WHERE name = :agent
            """
        ),
        {
            "agent": agent_name,
            "assigned": payload.retraining_assigned,
            "reason": payload.retraining_reason,
        },
    )
    db.commit()
    log.info(
        f"AGENT_RETRAINING agent={agent_name!r} assigned={payload.retraining_assigned} "
        f"reason_chars={len(payload.retraining_reason or '')} matched={result.rowcount}"
    )
    return {
        "updated": True,
        "matched_profiles": result.rowcount,
        "retraining_assigned": payload.retraining_assigned,
        "retraining_reason": payload.retraining_reason,
    }
