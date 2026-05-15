"""Bulletproof deal-linker — multi-tier match cascade run at intake time.

Goal: when a reviewer uploads a recording, decide whether it belongs to an
EXISTING customer + deal already in the system or whether to create a new
deal. The current upsert path (``intake.upsert.upsert_customer``) keys on
an exact slug of ``legal_name + trading_as`` which is too brittle — a
typo or punctuation change splits one customer across two rows. This
module fronts the upsert path with a deterministic-first / probabilistic-
second matcher whose output drives the route's deal-resolution branch.

Match cascade — in this exact order, first hit wins:

1. **Hard keys** (precision ≈ 1.0, no LLM needed):
     - MPAN electricity match (13-digit core)
     - MPRN gas match (6-10 digits)
     - DocuSign envelope reference match
     - Companies House number match
     - Charity Commission number match
   Any hit returns confidence = 1.0 with method = "hard_key:<which>".

2. **Composite probabilistic** (calibrated weighted-sum, threshold ≥ 0.85
   for review-queue and ≥ 0.99 for auto-merge):
     - cleanco-normalised + rapidfuzz token_set_ratio bucket on
       legal_name / trading_as
     - jellyfish metaphone equality on the first token
     - postcode full-match / outward-only / mismatch
     - supplier match / null / mismatch
     - recency within 90 days

3. **No match** → caller creates a new deal as before.

Returns ``MatchResult(deal_id, confidence, method, reason)`` or None.

All third-party deps are LAZY-IMPORTED so the module loads in test envs
that don't install rapidfuzz / jellyfish / cleanco. When a dep is missing
the matcher silently downgrades that signal — the cascade still works.
"""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.intake.payload_schema import CustomerMeta, DealMeta
from app.logger import log
from app.models import Customer, CustomerDeal


# ---------------------------------------------------------------------------
# Tunables — keep them at module level so tests and ops can introspect.
# ---------------------------------------------------------------------------

AUTO_MERGE_THRESHOLD = 0.99       # >= → auto-join the deal, no reviewer touch
REVIEW_QUEUE_THRESHOLD = 0.85     # [REVIEW_QUEUE, AUTO_MERGE) → candidate merge
NAME_NEAR_CERTAIN = 87            # rapidfuzz.token_set_ratio cut for "same name"
NAME_LIKELY = 75                  # below this, name contributes ~nothing
RECENCY_DAYS_FULL = 30            # any deal touched within N days = full weight
RECENCY_DAYS_HALF = 90            # within N days = partial weight
HARD_KEY_LOOKBACK_DAYS = 365      # how far back to scan for hard-key matches


@dataclass(frozen=True)
class MatchResult:
    """What the matcher returns to the upload route.

    ``deal_id`` is the resolved CustomerDeal.id (UUID) the new call should
    attach to. ``customer_id`` is the parent Customer (denormalised so the
    caller doesn't need to re-query). ``confidence`` is a calibrated 0-1
    posterior; ``method`` says which tier fired; ``reason`` is a short
    human-readable line that goes into the audit log + UI tooltip.
    """

    deal_id: uuid.UUID
    customer_id: Optional[uuid.UUID]
    confidence: float
    method: str
    reason: str


# ---------------------------------------------------------------------------
# Cheap deterministic helpers.
# ---------------------------------------------------------------------------


def _clean_name(raw: Optional[str]) -> str:
    """Strip legal-entity suffixes, lowercase, collapse punctuation/whitespace.

    Uses ``cleanco`` when available; falls back to a hand-rolled regex when
    it isn't (keeps the import optional for test envs).
    """
    if not raw:
        return ""
    s = raw.strip()
    if not s:
        return ""
    try:
        from cleanco import basename  # type: ignore[import-not-found]

        s = basename(s) or s
    except Exception:
        # Minimal hand-rolled suffix strip — keep parity with cleanco's
        # most common cases so the matcher degrades gracefully when the
        # dep isn't installed.
        for tail in (
            "ltd",
            "limited",
            "plc",
            "llp",
            "cic",
            "co",
            "company",
            "& co",
            "and co",
        ):
            pat = rf"\b{re.escape(tail)}\.?\s*$"
            s = re.sub(pat, "", s, flags=re.IGNORECASE)
    s = s.lower()
    # Drop the apostrophe so "Peter's" → "Peters" (instead of "Peter"),
    # then collapse remaining punctuation to spaces. We deliberately do
    # NOT strip the trailing 's' — "Peters / Peter" is a real-world UK
    # business-name pair (e.g. "St Peter's" vs "St Peters Benfleet")
    # and the 's' is high-signal for fuzz matching.
    s = s.replace("'", "").replace("`", "").replace("’", "")
    s = re.sub(r"[^\w\s]", " ", s)             # punctuation → space
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _token_set_ratio(a: str, b: str) -> int:
    """0-100 fuzz score. Returns 0 when rapidfuzz isn't installed."""
    if not a or not b:
        return 0
    try:
        from rapidfuzz import fuzz  # type: ignore[import-not-found]

        return int(fuzz.token_set_ratio(a, b))
    except Exception:
        # Token-set Jaccard as a backstop. Crude but never zero on
        # non-trivial overlap.
        ta, tb = set(a.split()), set(b.split())
        if not ta or not tb:
            return 0
        return int(100 * len(ta & tb) / max(len(ta | tb), 1))


def _metaphone(token: str) -> str:
    """Phonetic key for the first significant token. '' on dep miss."""
    if not token:
        return ""
    try:
        from jellyfish import metaphone  # type: ignore[import-not-found]

        return metaphone(token) or ""
    except Exception:
        # Soundex-ish fallback: keep first char + dropped vowels — coarse
        # but catches "Peters/Peter" without an external dep.
        t = token.lower()
        return (t[0] + re.sub(r"[aeiouhwy]", "", t[1:]))[:4]


def _first_token(name: str) -> str:
    parts = name.split()
    return parts[0] if parts else ""


def _norm_postcode(raw: Optional[str]) -> str:
    """UK postcode → canonical 'SW1A 1AA' or '' on missing/garbled."""
    if not raw:
        return ""
    s = re.sub(r"\s+", "", raw).upper()
    # Outward block (2-4 chars) + inward (3 chars). Tolerate inputs without
    # the space — typical reviewer entry.
    if 5 <= len(s) <= 7:
        return f"{s[:-3]} {s[-3:]}"
    return s


def _postcode_outward(pc: str) -> str:
    n = _norm_postcode(pc)
    return n.split(" ", 1)[0] if n else ""


# ---------------------------------------------------------------------------
# Meter ID canonicalisation.
# ---------------------------------------------------------------------------


def _mpan_core(raw: Optional[str]) -> str:
    """Extract the 13-digit MPAN core from any 13-or-21 digit string.

    UK MPAN core is the last 13 digits; the leading 8 digits of a full
    21-digit MPAN are profile/MTC/LLF metadata. We canonicalise to the
    13-digit core because the core is 1:1 with the physical meter point.
    Returns '' on missing or invalid length.
    """
    if not raw:
        return ""
    digits = "".join(ch for ch in str(raw) if ch.isdigit())
    if len(digits) == 13:
        return digits
    if len(digits) == 21:
        return digits[-13:]
    return ""


def _mprn_norm(raw: Optional[str]) -> str:
    if not raw:
        return ""
    digits = "".join(ch for ch in str(raw) if ch.isdigit())
    return digits if 6 <= len(digits) <= 10 else ""


def _docusign_norm(raw: Optional[str]) -> str:
    return (raw or "").strip().lower()


def _companies_house_norm(raw: Optional[str]) -> str:
    if not raw:
        return ""
    return re.sub(r"\s+", "", str(raw)).upper()


def _charity_norm(raw: Optional[str]) -> str:
    if not raw:
        return ""
    return re.sub(r"\s+", "", str(raw)).upper()


# ---------------------------------------------------------------------------
# Tier 1 — hard-key matcher.
# ---------------------------------------------------------------------------


def _hard_key_match(
    customer: CustomerMeta, deal: DealMeta, db: Session
) -> Optional[MatchResult]:
    """Deterministic. Returns a MatchResult with confidence=1.0 on the
    first hard key that hits, or None.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=HARD_KEY_LOOKBACK_DAYS)

    # MPAN core (electricity meter point — globally unique, never re-issued).
    mpan = _mpan_core(deal.mpan_electricity)
    if mpan:
        hit = (
            db.query(CustomerDeal)
            .filter(CustomerDeal.mpan_electricity.isnot(None))
            .filter(CustomerDeal.mpan_electricity != "")
            .filter(CustomerDeal.created_at >= cutoff)
            .all()
        )
        for d in hit:
            if _mpan_core(d.mpan_electricity) == mpan:
                return MatchResult(
                    deal_id=d.id,
                    customer_id=d.customer_id,
                    confidence=1.0,
                    method="hard_key:mpan",
                    reason=f"MPAN core {mpan} matches existing deal",
                )

    # MPRN (gas — nationally unique).
    mprn = _mprn_norm(deal.mprn_gas)
    if mprn:
        hit = (
            db.query(CustomerDeal)
            .filter(CustomerDeal.mprn_gas.isnot(None))
            .filter(CustomerDeal.mprn_gas != "")
            .filter(CustomerDeal.created_at >= cutoff)
            .all()
        )
        for d in hit:
            if _mprn_norm(d.mprn_gas) == mprn:
                return MatchResult(
                    deal_id=d.id,
                    customer_id=d.customer_id,
                    confidence=1.0,
                    method="hard_key:mprn",
                    reason=f"MPRN {mprn} matches existing deal",
                )

    # DocuSign envelope reference — per-document unique once the LOA is sent.
    ds = _docusign_norm(deal.docusign_reference)
    if ds:
        hit = (
            db.query(CustomerDeal)
            .filter(CustomerDeal.docusign_reference.isnot(None))
            .filter(CustomerDeal.docusign_reference != "")
            .filter(CustomerDeal.created_at >= cutoff)
            .all()
        )
        for d in hit:
            if _docusign_norm(d.docusign_reference) == ds:
                return MatchResult(
                    deal_id=d.id,
                    customer_id=d.customer_id,
                    confidence=1.0,
                    method="hard_key:docusign",
                    reason="DocuSign envelope reference matches",
                )

    # Companies House number on the CUSTOMER side (multiple deals possible
    # under one company; we attach to the most recent open deal of that
    # customer, NOT every deal — handled in the composite path below).
    cn = _companies_house_norm(customer.company_number)
    ch = _charity_norm(customer.charity_number)
    if cn or ch:
        q = db.query(Customer)
        if cn and ch:
            q = q.filter(
                or_(Customer.company_number == cn, Customer.charity_number == ch)
            )
        elif cn:
            q = q.filter(Customer.company_number == cn)
        else:
            q = q.filter(Customer.charity_number == ch)
        hit_customer = q.first()
        if hit_customer:
            # Pick the most recent OPEN deal under this customer.
            deal_row = (
                db.query(CustomerDeal)
                .filter(CustomerDeal.customer_id == hit_customer.id)
                .filter(CustomerDeal.status == "in_progress")
                .order_by(CustomerDeal.created_at.desc())
                .first()
            )
            if deal_row:
                key_name = "company_number" if cn else "charity_number"
                key_val = cn or ch
                return MatchResult(
                    deal_id=deal_row.id,
                    customer_id=hit_customer.id,
                    confidence=1.0,
                    method=f"hard_key:{key_name}",
                    reason=f"{key_name} {key_val} matches existing customer",
                )

    return None


# ---------------------------------------------------------------------------
# Tier 2 — composite probabilistic match.
# ---------------------------------------------------------------------------


def _score_pair(
    incoming_clean_name: str,
    incoming_first_metaphone: str,
    incoming_postcode_full: str,
    incoming_postcode_out: str,
    incoming_supplier: Optional[str],
    incoming_now: datetime,
    deal: CustomerDeal,
    customer_row: Optional[Customer],
) -> tuple[float, list[str]]:
    """Calibrated weighted-sum scorer. Returns (score, evidence_lines).

    Weights are tuned to land MPAN-less but name+postcode-confirming
    multi-call deals at ≥0.99 and "same supplier, similar name only"
    candidates in the 0.85-0.99 review band. Anything weaker lands <0.85.
    """
    score = 0.0
    evidence: list[str] = []

    candidate_names = []
    if customer_row:
        candidate_names.append(_clean_name(customer_row.legal_name))
        if customer_row.trading_as:
            candidate_names.append(_clean_name(customer_row.trading_as))
    candidate_names.append(_clean_name(deal.customer_name))
    candidate_names = [n for n in candidate_names if n]

    best_name_ratio = 0
    for cn in candidate_names:
        r = _token_set_ratio(incoming_clean_name, cn)
        if r > best_name_ratio:
            best_name_ratio = r

    # Name score — heavy weight on near-certain matches. Tuned so:
    #   name(>=95) alone               ~ 0.80   → below review (safe)
    #   name(>=95) + supplier          ~ 0.86   → review band
    #   name(>=95) + postcode(full)    ~ 1.00   → auto-merge
    #   name(>=87) + supplier + pcfull ~ 0.99   → auto-merge
    if best_name_ratio >= 95:
        score += 0.62
        evidence.append(f"name token_set_ratio={best_name_ratio} (near-certain)")
    elif best_name_ratio >= NAME_NEAR_CERTAIN:
        score += 0.50
        evidence.append(f"name token_set_ratio={best_name_ratio} (strong)")
    elif best_name_ratio >= NAME_LIKELY:
        score += 0.25
        evidence.append(f"name token_set_ratio={best_name_ratio} (weak)")
    elif best_name_ratio > 0:
        evidence.append(f"name token_set_ratio={best_name_ratio} (noise)")

    # Metaphone first-token — small independent signal.
    if incoming_first_metaphone and candidate_names:
        candidate_metaphones = {
            _metaphone(_first_token(n)) for n in candidate_names if n
        }
        if incoming_first_metaphone in candidate_metaphones:
            score += 0.08
            evidence.append("metaphone(first_token) match")

    # Postcode — strong corroborator when present.
    if customer_row and customer_row.address_postcode:
        d_full = _norm_postcode(customer_row.address_postcode)
        d_out = _postcode_outward(customer_row.address_postcode)
        if incoming_postcode_full and d_full and incoming_postcode_full == d_full:
            score += 0.25
            evidence.append(f"postcode full match {d_full}")
        elif incoming_postcode_out and d_out and incoming_postcode_out == d_out:
            score += 0.08
            evidence.append(f"postcode outward match {d_out}")

    # Supplier — low weight (only 14 suppliers).
    if incoming_supplier and deal.supplier:
        if incoming_supplier.strip().lower() == deal.supplier.strip().lower():
            score += 0.06
            evidence.append(f"supplier match {deal.supplier!r}")

    # Recency — broker pipelines complete within ~30 days; older overlaps
    # of name are likely *different* deals (re-engagement at renewal).
    if deal.created_at:
        # CustomerDeal.created_at on this codebase is timezone-NAIVE on
        # Postgres TIMESTAMP — normalise both sides to UTC-aware.
        dca = deal.created_at
        if dca.tzinfo is None:
            dca = dca.replace(tzinfo=timezone.utc)
        delta_days = (incoming_now - dca).days
        if delta_days <= RECENCY_DAYS_FULL:
            score += 0.10
            evidence.append(f"within {RECENCY_DAYS_FULL}d")
        elif delta_days <= RECENCY_DAYS_HALF:
            score += 0.04
            evidence.append(f"within {RECENCY_DAYS_HALF}d")
        else:
            evidence.append(f"older than {RECENCY_DAYS_HALF}d (-)")

    # Hard guardrail: a low name ratio AND no postcode AND no supplier match
    # is structurally a different customer — clamp the score so we never
    # auto-merge on weak coincidence.
    if best_name_ratio < NAME_LIKELY:
        score = min(score, 0.5)

    # Status guardrail: closed/lost deals should not absorb a new call —
    # the new call is a different transaction.
    if deal.status not in ("in_progress", None, "open"):
        score = min(score, 0.6)
        evidence.append(f"deal.status={deal.status!r} (capped)")

    return round(min(score, 1.0), 4), evidence


def _composite_match(
    customer: CustomerMeta, deal: DealMeta, db: Session
) -> Optional[MatchResult]:
    """Probabilistic. Scans recent open deals; scores each; returns the
    best whose score >= REVIEW_QUEUE_THRESHOLD. Below the auto-merge
    threshold the caller can route to the candidate-merge queue.
    """
    incoming_name = _clean_name(customer.legal_name) or _clean_name(
        customer.trading_as
    )
    if not incoming_name and not customer.address_postcode:
        # Nothing to fuzz on. Skip composite tier.
        return None

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=RECENCY_DAYS_HALF)

    # Blocking — keep this cheap. Pull recent in_progress deals.
    candidates = (
        db.query(CustomerDeal)
        .filter(CustomerDeal.created_at >= cutoff.replace(tzinfo=None))
        .order_by(CustomerDeal.created_at.desc())
        .limit(500)
        .all()
    )
    if not candidates:
        return None

    incoming_first_metaphone = _metaphone(_first_token(incoming_name))
    incoming_pc_full = _norm_postcode(customer.address_postcode)
    incoming_pc_out = _postcode_outward(customer.address_postcode)
    incoming_supplier = deal.supplier.value if deal.supplier is not None else None

    # Resolve customer rows in a single query to avoid N+1.
    customer_ids = {c.customer_id for c in candidates if c.customer_id}
    customer_map: dict[uuid.UUID, Customer] = {}
    if customer_ids:
        for c in db.query(Customer).filter(Customer.id.in_(customer_ids)).all():
            customer_map[c.id] = c

    best: Optional[tuple[float, list[str], CustomerDeal]] = None
    for cand in candidates:
        cust_row = customer_map.get(cand.customer_id) if cand.customer_id else None
        score, evidence = _score_pair(
            incoming_clean_name=incoming_name,
            incoming_first_metaphone=incoming_first_metaphone,
            incoming_postcode_full=incoming_pc_full,
            incoming_postcode_out=incoming_pc_out,
            incoming_supplier=incoming_supplier,
            incoming_now=now,
            deal=cand,
            customer_row=cust_row,
        )
        if best is None or score > best[0]:
            best = (score, evidence, cand)

    if best is None or best[0] < REVIEW_QUEUE_THRESHOLD:
        return None

    score, evidence, deal_row = best
    method = "composite_auto" if score >= AUTO_MERGE_THRESHOLD else "composite_review"
    return MatchResult(
        deal_id=deal_row.id,
        customer_id=deal_row.customer_id,
        confidence=score,
        method=method,
        reason="; ".join(evidence)[:240] or "composite features",
    )


# ---------------------------------------------------------------------------
# Public entrypoint.
# ---------------------------------------------------------------------------


def find_existing_deal(
    customer: CustomerMeta, deal: DealMeta, db: Session
) -> Optional[MatchResult]:
    """Single-call API used by the upload route.

    Tier 1 (hard keys) runs first; on hit, returns immediately with
    confidence=1.0. Tier 2 (composite) runs otherwise. Returns None
    when neither tier produces a candidate ≥ REVIEW_QUEUE_THRESHOLD.
    Caller decides what to do at each band:
      * confidence ≥ AUTO_MERGE_THRESHOLD → silently attach
      * REVIEW_QUEUE_THRESHOLD ≤ c < AUTO_MERGE_THRESHOLD → attach + flag
        for reviewer confirmation in the candidate-merge queue
      * None → create a new deal (legacy upsert path)
    """
    # Sanity: if the caller already supplied existing_deal_id, the route
    # short-circuits before us. Defensive double-check anyway.
    if deal.existing_deal_id:
        return None
    try:
        hit = _hard_key_match(customer, deal, db)
        if hit:
            log.info(
                f"\U0001f517 MATCH hard method={hit.method} "
                f"deal_id={str(hit.deal_id)[:8]} "
                f"customer={(customer.legal_name or '?')!r}"
            )
            return hit
        hit = _composite_match(customer, deal, db)
        if hit:
            log.info(
                f"\U0001f517 MATCH composite method={hit.method} "
                f"conf={hit.confidence:.3f} deal_id={str(hit.deal_id)[:8]} "
                f"reason={hit.reason!r}"
            )
            return hit
    except Exception as e:
        # Matcher must NEVER block an upload. Fall through to legacy path.
        log.warning(f"matcher failed (ignored, falling back to legacy): {e}")
        return None
    return None
