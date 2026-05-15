"""Business-name detection. Distinct from detect_names (which finds the
agent/customer pair of people) — extracts the non-person entity (the
business the call is about). Used downstream by pipeline.detect_metadata
to fuzzy-merge auto-detect uploads onto existing Customer rows."""
from __future__ import annotations

from difflib import SequenceMatcher

from sqlalchemy.orm import Session

from app.analysis import _call_llm
from app.logger import log
from app.models import Customer


_PROMPT = """Read this UK energy-brokerage call (Watt Utilities / TPI calling a
business about their gas or electricity contract). Extract the LEGAL NAME OF
THE BUSINESS that is the customer of this call.

DEFINITIONS
- Business name = the company / sole trader / charity / school / care home
  / church / restaurant / etc. whose energy account is the subject of the
  call. The agent is reaching out to that BUSINESS, even though they speak
  to an individual representative.
- This is NOT the person on the phone (e.g. "Andrew", "Jay", "Bradley").
- This is NOT the supplier (e.g. "British Gas", "E.ON Next", "EDF").
- This is NOT the broker / Watt Utilities / TPI / aggregator.

HOW TO FIND IT
The agent typically reads the business name out loud near the top of the
recording while confirming the account holder. Common phrasings:
  • "you're still down as <BUSINESS>, is that right?"
  • "the company name for me as well please" / "I'm calling about <BUSINESS>"
  • "this LOA is for <BUSINESS>" / "for the supply at <BUSINESS>"
  • "calling on behalf of <BUSINESS>" / "calling about your account with [SUPPLIER]
     under <BUSINESS>"
Customers also self-identify ("yeah it's <BUSINESS>", "<BUSINESS>, my company").

Be liberal about legal-form suffixes — keep them in the answer:
  Limited / Ltd / LLP / LLC / PLC / CIC / Inc / Trust / Foundation /
  Association / Society / Group / Holdings / Properties / Estates /
  Lodge / Hotel / Hall / Church / Centre / Trading As / T/A / Ta

FORMAT
Respond with ONLY the business name on a single line, no JSON, no prose,
no "BUSINESS:" prefix.

If after reading the WHOLE transcript you cannot find a business name —
or the only candidates are clearly the supplier or broker — output
exactly: Unknown

EXAMPLES OF GOOD OUTPUTS
  Clifton Rest Home Association
  Awais Mustafa Ta Shah's Palace
  Fast Fix Drainage and Plumbing Limited
  Evangelical Church
  St Peters Church
  Bob's Glazing Limited
  Josephs Estate Agents Ltd
  P E M Plant Chem Inter Ltd

Transcript:
{transcript_start}

Business name:"""


# Words that look like business candidates by shape but aren't real ones —
# the LLM occasionally falls through to outputting the customer person's
# name when no real business name was spoken. We reject those so the
# pipeline doesn't end up with a deal named "Jashri" or "Jay Shree".
_PERSON_NAME_LIKE = {
    # Common first names that the LLM tends to confuse for business names
    # when transcripts are vague. Not exhaustive — covers the ones we've
    # actually seen mis-extracted on the AI Data fixtures.
    "andrew", "andy", "bradley", "bradley clayton", "dinesh", "dinesh gurung",
    "jashri", "jay", "jay shree", "jayanthi", "jayanthi swaminathan",
    "keith", "keith tandy", "sammy", "tom", "tom kelly", "alex", "ethan",
    "callum", "francis", "jack", "jack giles", "leslie", "simon", "wayne",
    "paige", "parat", "afak", "sean", "sean robbins", "dominic",
}


def _looks_like_person_name(name: str) -> bool:
    """Return True when the candidate is plausibly a person's name rather
    than a business. Used as a guard so the LLM hallucinating a first-name
    doesn't poison the deal-linker. We're conservative — only veto if BOTH:
      (a) the name has no legal-form suffix (Ltd / Hotel / Church / etc.)
      (b) the lowercased name is in the known-person stop-set.
    """
    cleaned = name.strip().lower()
    if not cleaned:
        return True
    LEGAL_FORM_HINTS = (
        " ltd", " limited", " llp", " plc", " cic", " inc", " trust", " ta ", " t/a",
        " group", " holdings", " properties", " estates", " hotel", " hall",
        " church", " association", " society", " foundation", " centre",
        " school", " home", " lodge", " house", " restaurant", " store",
        " shop", " bar", " cafe", " gym", " surgery", " clinic", " practice",
        " salon", " spa", " pub", " inn", "&", " and ", " of ",
    )
    if any(hint in cleaned for hint in LEGAL_FORM_HINTS):
        return False
    if cleaned in _PERSON_NAME_LIKE:
        return True
    # Pure single-token first-name shape with no spaces is also suspect.
    if " " not in cleaned and len(cleaned) <= 15:
        return True
    return False


async def detect_business_name(transcript: str) -> str | None:
    """Return the business name spoken in the call, or None if unclear.

    Failures are swallowed and return None — never propagate to the caller.
    The pipeline keeps running with no business name and the auto-detect
    stub deal stays as fallback.
    """
    words = transcript.split()
    # Use a wider window than the old 600 because business names often
    # land in the verbal-contract reading further into the call.
    transcript_start = " ".join(words[:1500])
    prompt = _PROMPT.replace("{transcript_start}", transcript_start)
    try:
        # Cheap classification — Sonnet is plenty for this task and ~5x
        # cheaper than Opus on output tokens.
        result = await _call_llm(prompt, timeout=20.0, cheap=True)
    except Exception as e:
        log.warning(f"\U0001f3e2 BUSINESS_DETECT failed: {e}")
        return None
    name = result.strip().strip('"').strip()
    # Strip a "Business name:" prefix the model occasionally leaks.
    if name.lower().startswith("business name:"):
        name = name.split(":", 1)[1].strip().strip('"').strip()
    if not name or name == "Unknown":
        return None
    if _looks_like_person_name(name):
        log.warning(
            f"\U0001f3e2 BUSINESS_DETECT rejected person-name {name!r} — using None"
        )
        return None
    log.info(f"\U0001f3e2 BUSINESS_DETECT → {name!r}")
    return name


def fuzzy_match_customer(
    name: str | None,
    db: Session,
    threshold: float = 0.6,
) -> Customer | None:
    """Return the highest-similarity Customer.legal_name match, or None
    if nothing clears the threshold.

    Used by pipeline.detect_metadata to collapse stub deals onto existing
    Customer rows when the AI-detected business name resembles a stored
    legal_name.

    Implementation note: the original sprint plan called for pg_trgm
    ``similarity()``, but that extension is unavailable on the Supabase
    pooler (see migration f1a2b3c4d5e6 header — "fuzzy search uses ILIKE
    instead"). We score on the Python side via ``difflib.SequenceMatcher``
    — same approach used in ``app.verification.fuzzy_match`` — which works
    in both SQLite tests and Postgres production with no extension
    dependency. n stays small (one customer table scan) so this is fine.
    """
    if not name or not name.strip():
        return None

    target = name.strip().lower()
    best: Customer | None = None
    best_score = 0.0
    for customer in db.query(Customer).all():
        legal = (customer.legal_name or "").lower()
        if not legal:
            continue
        score = SequenceMatcher(None, target, legal).ratio()
        if score >= threshold and score > best_score:
            best = customer
            best_score = score
    return best
