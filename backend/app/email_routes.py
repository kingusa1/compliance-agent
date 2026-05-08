"""W3.B (v3-watt-coverage) — Customer confirmation email endpoint.

Compliance manual §8 mandates that every accepted verbal contract is
followed by a customer-facing confirmation email restating the agent-
quoted unit rate + standing charge, the contract length, the 14-day
cooling-off period, and a reference to the signed DocuSign envelope.

This module sits next to ``directives_routes`` (which already owns the
``/feedback-email`` endpoint targeting the agent for internal coaching).
We deliberately do NOT extend that file because the audiences differ —
internal vs customer — and the demo's terminal log greps on the SEND
event name, which we keep distinct (``CUSTOMER_CONFIRMATION_EMAIL_SEND``
vs ``FEEDBACK_EMAIL_SEND``) for unambiguous filtering.

Send is a stub — the existing ``/feedback-email`` handler also stub-
sends and just logs the event. Once a real SMTP / SES wire-up lands
both endpoints will adopt it together.

Contract
--------
POST /api/calls/{call_id}/customer-email   (auth required)
Body :: { "to": str | None, "cc": list[str] | None }
Resp :: {
  "sent": bool,
  "message_id": str,
  "preview_html": str,
}

Failure mode
------------
Per W3.B failure-mode plan: when a required field cannot be located
(no customer email, no extracted unit rate, no DocuSign envelope, ...)
we substitute the literal token ``{{ MISSING: <key> }}`` into the
rendered HTML and emit a structured ``CUSTOMER_EMAIL_MISSING_FIELDS``
warning so the data team can backfill. We never raise — the reviewer
must always be able to preview what would be sent.
"""
from __future__ import annotations

import logging
import uuid
from collections import defaultdict
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

# Same email regex shape used by directives_routes.FeedbackEmailIn — keep
# the two endpoints validating the same way so reviewer experience matches.
_EMAIL_RE = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"

from app.auth import current_user
from app.database import get_db
from app.logger import log
from app.models import Call, Customer, CustomerDeal, ExtractedEntity


email_router = APIRouter(tags=["email"])


# ── template loading ─────────────────────────────────────────────

_TEMPLATE_PATH = Path(__file__).parent / "templates" / "customer_confirmation.html"
_TEMPLATE_CACHE: str | None = None


def _load_template() -> str:
    """Read the customer-confirmation template once and cache in-process.

    Reading on every call would be wasteful but is harmless; caching just
    saves the syscall under load. Tests can clear the cache by reaching
    in if they ever change the file at runtime.
    """
    global _TEMPLATE_CACHE
    if _TEMPLATE_CACHE is None:
        _TEMPLATE_CACHE = _TEMPLATE_PATH.read_text(encoding="utf-8")
    return _TEMPLATE_CACHE


# ── placeholder defaultdict ─────────────────────────────────────

class _MissingDict(dict):
    """str.format_map source that turns absent keys into a visible
    placeholder rather than crashing the format call."""

    def __init__(self, data: dict[str, Any]):
        super().__init__()
        self._missing: list[str] = []
        for k, v in data.items():
            if v is None or (isinstance(v, str) and not v.strip()):
                self._missing.append(k)
                self[k] = f"{{{{ MISSING: {k} }}}}"
            else:
                self[k] = v

    def __missing__(self, key: str) -> str:
        # str.format_map calls __missing__ when a placeholder isn't in
        # the dict — record it and return a visible token.
        self._missing.append(key)
        return f"{{{{ MISSING: {key} }}}}"

    @property
    def missing_keys(self) -> list[str]:
        return list(self._missing)


# ── data assembly ───────────────────────────────────────────────

def _resolve_customer_email(db: Session, call: Call) -> str | None:
    """Best-effort: there is no first-class customer-email column today
    (Customer model has legal_name + slug + postcode but no email). Until
    the schema gains one we surface the reviewer-supplied override only,
    and the auto-resolve path returns None — which the template renders
    as a MISSING placeholder. Documented in W3.B verification notes."""
    return None


def _format_term_months(months: int | None) -> str | None:
    if months is None:
        return None
    if months % 12 == 0:
        years = months // 12
        return f"{months} months ({years} year{'s' if years != 1 else ''})"
    return f"{months} months"


def _format_money(value: Any, suffix: str = "") -> str | None:
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    return f"£{s}{suffix}"


def _gather_template_vars(
    db: Session,
    call: Call,
    sender: dict,
) -> _MissingDict:
    """Pull every placeholder the template needs from the DB. None /
    blank values become ``{{ MISSING: <key> }}`` via ``_MissingDict``."""
    deal: CustomerDeal | None = None
    customer: Customer | None = None
    if call.deal_id:
        deal = db.query(CustomerDeal).filter(CustomerDeal.id == call.deal_id).one_or_none()
        if deal and deal.customer_id:
            customer = db.query(Customer).filter(Customer.id == deal.customer_id).one_or_none()

    customer_name = (
        (customer.legal_name if customer else None)
        or call.customer_name
        or (deal.customer_name if deal else None)
    )
    supplier = (
        (deal.supplier if deal else None)
        or call.detected_supplier
    )
    contract_length = _format_term_months(deal.term_months if deal else None)
    docusign_ref = (deal.docusign_reference if deal else None) or None
    call_ref = call.call_ref or call.id

    # Pricing — pull from extracted_entities; the W3.A pricing extractor
    # writes ``unit_rate`` + ``standing_charge`` keys when wired, today
    # only annual_cost / commission / deal_value_gbp are present, so
    # these will typically render as MISSING until W3.A lands.
    rates = {
        e.key: e.value
        for e in db.query(ExtractedEntity).filter(ExtractedEntity.call_id == call.id).all()
    }
    unit_rate = _format_money(rates.get("unit_rate"), suffix="p / kWh") if rates.get("unit_rate") else None
    standing_charge = (
        _format_money(rates.get("standing_charge"), suffix="p / day")
        if rates.get("standing_charge") else None
    )

    return _MissingDict({
        "customer_name": customer_name,
        "supplier": supplier,
        "contract_length": contract_length,
        "unit_rate": unit_rate,
        "standing_charge": standing_charge,
        "docusign_ref": docusign_ref,
        "call_ref": call_ref,
        "sender_name": sender.get("name") or sender.get("email") or "Compliance team",
        "sender_email": sender.get("email") or "compliance@xaia.ae",
    })


# ── schemas ─────────────────────────────────────────────────────

class CustomerEmailIn(BaseModel):
    to: str | None = Field(default=None, pattern=_EMAIL_RE, description="Override recipient. If omitted, uses the customer record on file.")
    cc: list[str] = Field(default_factory=list)


class CustomerEmailOut(BaseModel):
    sent: bool
    message_id: str
    preview_html: str
    to: str | None = None
    cc: list[str] = Field(default_factory=list)
    missing_fields: list[str] = Field(default_factory=list)


# ── endpoint ────────────────────────────────────────────────────

def send_customer_email_for_call(
    *,
    db: Session,
    call_id: str,
    to: str | None = None,
    cc: list[str] | None = None,
    sender: dict | None = None,
) -> dict:
    """Render + send (stub) the customer-confirmation email for a single call.

    Sprint A2 — extracted out of ``send_customer_email`` so non-route code
    paths (e.g. the auto-fire on PASS verdict in ``hitl_routes.submit_verdict``)
    can trigger the same email pipeline without spinning up an HTTP client.

    Always returns a dict; never raises. Failures (missing call, missing
    recipient, template error) log a warning and return ``sent=False`` so
    the caller's verdict-submit path can never be blocked by an email
    side-effect.
    """
    call = db.query(Call).filter(Call.id == call_id).one_or_none()
    if call is None:
        log.warning(
            "CUSTOMER_EMAIL_CALL_NOT_FOUND call_id=%s", call_id,
        )
        return {
            "sent": False,
            "message_id": "",
            "preview_html": "",
            "to": None,
            "cc": [],
            "missing_fields": ["call"],
        }

    sender_dict = sender or {"email": "system@compliance-agent", "name": "Compliance system"}
    try:
        vars_dict = _gather_template_vars(db, call, sender=sender_dict)
        template = _load_template()
        html = template.format_map(vars_dict)
    except Exception as e:  # pragma: no cover — template render is defensive
        log.warning(
            "CUSTOMER_EMAIL_RENDER_FAILED call_id=%s err=%s", call_id, e,
        )
        return {
            "sent": False,
            "message_id": "",
            "preview_html": "",
            "to": to,
            "cc": list(cc or []),
            "missing_fields": ["render_error"],
        }

    to_addr = to or _resolve_customer_email(db, call)
    cc_list = list(cc or [])

    if vars_dict.missing_keys:
        log.warning(
            "CUSTOMER_EMAIL_MISSING_FIELDS call_id=%s missing=%s",
            call_id,
            sorted(set(vars_dict.missing_keys)),
        )

    message_id = f"msg_{uuid.uuid4().hex[:16]}"
    log.info(
        "CUSTOMER_CONFIRMATION_EMAIL_SEND call_id=%s to=%s cc=%s "
        "missing=%d sender=%s message_id=%s",
        call_id,
        to_addr or "<none>",
        ",".join(cc_list) or "<none>",
        len(set(vars_dict.missing_keys)),
        sender_dict.get("email") or sender_dict.get("id") or "<unknown>",
        message_id,
    )

    return {
        "sent": bool(to_addr),
        "message_id": message_id,
        "preview_html": html,
        "to": to_addr,
        "cc": cc_list,
        "missing_fields": sorted(set(vars_dict.missing_keys)),
    }


@email_router.post(
    "/api/calls/{call_id}/customer-email",
    response_model=CustomerEmailOut,
)
def send_customer_email(
    call_id: str,
    payload: CustomerEmailIn,
    user: dict = Depends(current_user),
    db: Session = Depends(get_db),
) -> CustomerEmailOut:
    """Send (stub) a customer-facing confirmation email and return the
    rendered HTML so the Verdict tab's preview card can show exactly
    what the customer would receive."""
    # Surface a 404 for the route layer (the helper returns {"missing_fields":
    # ["call"]} but doesn't raise — the HTTP contract still wants a 404).
    call = db.query(Call).filter(Call.id == call_id).one_or_none()
    if not call:
        raise HTTPException(404, "call not found")

    result = send_customer_email_for_call(
        db=db,
        call_id=call_id,
        to=payload.to,
        cc=payload.cc,
        sender=user,
    )
    return CustomerEmailOut(
        sent=result["sent"],
        message_id=result["message_id"],
        preview_html=result["preview_html"],
        to=result.get("to"),
        cc=result.get("cc") or [],
        missing_fields=result.get("missing_fields") or [],
    )
