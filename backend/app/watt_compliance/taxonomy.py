"""Compliance taxonomy — single source of truth for rejection categories,
detailed reasons, severity tiers, and verdict actions.

Derived from:
- `Compliance Xai rejection lists.xlsx` (real rejection feedback)
- `Compliance tracker example.xlsx` (the 4 master Category values)
- `WATT SALES COMPLIANCE GUIDE.pdf` (the 8 Standards + 27 reasons)
- `Watt_AI_Phrase_Detection_Dataset.docx` (severity → action mapping)
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class RejectionCategory(str, Enum):
    """The 4 master categories from the operations team's tracker XLSX.

    Every rejection MUST land in exactly one of these. Detailed reason
    (RejectionReason below) refines the why; this is the operations
    bucket used in the tracker dashboard.
    """

    ADMIN_ERROR = "ADMIN_ERROR"           # Wrong name / company details / postcode / MPAN
    PROCESS_FAILURE = "PROCESS_FAILURE"   # BACS denied / no LOA / debt / domestic meter
    COMPLIANCE_ISSUE = "COMPLIANCE_ISSUE" # Identity not stated / vulnerable customer / no authority
    VERBAL_SALES_ERROR = "VERBAL_SALES_ERROR"  # Missed lines / wrong rates / DD guarantee / rushed


class Severity(str, Enum):
    """3-tier severity from the Phrase Detection Dataset."""

    CRITICAL = "CRITICAL"   # → BLOCK + escalate
    HIGH = "HIGH"           # → REVIEW (manual)
    MEDIUM = "MEDIUM"       # → COACH (training note only)


class VerdictAction(str, Enum):
    """The action the system takes when a rejection is raised."""

    BLOCK = "BLOCK"
    REVIEW = "REVIEW"
    COACH = "COACH"


SEVERITY_TO_ACTION: dict[Severity, VerdictAction] = {
    Severity.CRITICAL: VerdictAction.BLOCK,
    Severity.HIGH: VerdictAction.REVIEW,
    Severity.MEDIUM: VerdictAction.COACH,
}


class RiskTag(str, Enum):
    """The 4 canonical risk tags from the system spec (TS §9)."""

    OMBUDSMAN_RISK = "ombudsman_risk"
    MIS_SELLING_RISK = "mis_selling_risk"
    COMPLAINT_RISK = "complaint_risk"
    CANCELLATION_RISK = "cancellation_risk"


class CallType(str, Enum):
    """Watt-canonical call types — matches frontend CallType enum and
    backend deal_lifecycle.py phase model.

    `standalone_loa` is the deal-lifecycle phase name; the upload form
    uses `loa` and the L7Form maps it through.
    """

    LEAD_GEN = "lead_gen"
    PASSOVER = "passover"
    CLOSER = "closer"
    VERBAL = "verbal"
    LOA = "loa"
    STANDALONE_LOA = "standalone_loa"  # legacy alias
    C_CALL = "c_call"
    AMENDMENT = "amendment"
    FULL = "full"


@dataclass(frozen=True)
class RejectionReason:
    """One of the 27 detailed reasons from the WATT SALES COMPLIANCE GUIDE."""

    code: str  # R01..R27
    title: str
    category: RejectionCategory
    default_severity: Severity
    standard: int  # Which of the 8 Standards this maps to (or 0 = ops-derived)
    description: str


# 27 detailed reasons. Mapping to Category derived from:
#   - the actual rejection list XLSX (which kinds of issue land where in
#     practice)
#   - Watt Sales Compliance Guide §3 (severity matrix narrative)
REJECTION_REASONS: tuple[RejectionReason, ...] = (
    RejectionReason("R01", "Identity / Opening Failure", RejectionCategory.COMPLIANCE_ISSUE, Severity.CRITICAL, 1,
                    "Agent did not identify themselves, their company, or the purpose of the call."),
    RejectionReason("R02", "False / Misleading Opening Claim", RejectionCategory.COMPLIANCE_ISSUE, Severity.HIGH, 1,
                    "Agent made inaccurate claims about company size, market share, or capabilities."),
    RejectionReason("R03", "Unwanted Contact Continued", RejectionCategory.COMPLIANCE_ISSUE, Severity.HIGH, 2,
                    "Agent continued contact after the customer clearly indicated they did not wish to continue."),
    RejectionReason("R04", "Vulnerable Customer Not Identified / Not Handled", RejectionCategory.COMPLIANCE_ISSUE, Severity.CRITICAL, 2,
                    "Vulnerability signs were present but agent did not slow down, offer callback, or seek management support."),
    RejectionReason("R05", "High-Pressure / Coercive Tactics", RejectionCategory.COMPLIANCE_ISSUE, Severity.CRITICAL, 3,
                    "Agent used coercion, harassment, or undue pressure to secure the sale."),
    RejectionReason("R06", "Misleading / Deceptive Information", RejectionCategory.COMPLIANCE_ISSUE, Severity.CRITICAL, 3,
                    "Agent provided false, deceptive, or reasonably-misleading information."),
    RejectionReason("R07", "Commission Not Disclosed", RejectionCategory.COMPLIANCE_ISSUE, Severity.HIGH, 3,
                    "Agent stated 'you don't pay' or 'we are paid by the supplier' without explaining commission is in the unit rate."),
    RejectionReason("R08", "Market Comparison Misrepresentation", RejectionCategory.COMPLIANCE_ISSUE, Severity.HIGH, 3,
                    "Agent claimed 'best price' or 'full market search' when only a partial search was done."),
    RejectionReason("R09", "Unsubstantiated Price Prediction", RejectionCategory.COMPLIANCE_ISSUE, Severity.HIGH, 3,
                    "Agent made forward-looking price claims without evidential substantiation."),
    RejectionReason("R10", "Competitor / Industry Misinformation", RejectionCategory.COMPLIANCE_ISSUE, Severity.HIGH, 3,
                    "Agent made statements about other suppliers or industry events without public-domain or verifiable source."),
    RejectionReason("R11", "No Authority Check", RejectionCategory.COMPLIANCE_ISSUE, Severity.CRITICAL, 4,
                    "Agent did not confirm the customer had authority to enter a legally binding contract."),
    RejectionReason("R12", "Domestic Customer Contracted", RejectionCategory.PROCESS_FAILURE, Severity.CRITICAL, 4,
                    "Agent contracted a customer whose usage is wholly or mainly domestic."),
    RejectionReason("R13", "Prepayment Meter Customer Contracted", RejectionCategory.PROCESS_FAILURE, Severity.CRITICAL, 4,
                    "Agent contracted a customer with a prepayment meter, which Watt cannot support."),
    RejectionReason("R14", "Principal Terms Not Explained", RejectionCategory.VERBAL_SALES_ERROR, Severity.HIGH, 5,
                    "Agent did not explain and obtain acknowledgement of the principal contract terms."),
    RejectionReason("R15", "Customer Did Not Understand Transfer", RejectionCategory.VERBAL_SALES_ERROR, Severity.HIGH, 6,
                    "Customer did not demonstrate understanding they were entering a supply transfer contract."),
    RejectionReason("R16", "Pricing / Tariff / Charges Not Understood", RejectionCategory.VERBAL_SALES_ERROR, Severity.HIGH, 6,
                    "Customer did not demonstrate understanding of all applicable prices, charges, tariffs, service levels."),
    RejectionReason("R17", "Script Not Followed / Incomplete", RejectionCategory.VERBAL_SALES_ERROR, Severity.HIGH, 7,
                    "Agent deviated from, rushed, glossed over, or did not complete the full Watt verbal contract script."),
    RejectionReason("R18", "Agent Answered for Customer", RejectionCategory.VERBAL_SALES_ERROR, Severity.CRITICAL, 7,
                    "Agent answered script questions on behalf of the customer."),
    RejectionReason("R19", "Wrong Script Used", RejectionCategory.VERBAL_SALES_ERROR, Severity.CRITICAL, 7,
                    "Agent used a script not appropriate for the occasion or not the current Watt-approved version."),
    RejectionReason("R20", "Contract / LOA Incomplete or Illegible", RejectionCategory.ADMIN_ERROR, Severity.HIGH, 8,
                    "Paper contract or LOA is missing information, illegible, or not signed."),
    RejectionReason("R21", "LOA Amendment Not Re-Signed", RejectionCategory.ADMIN_ERROR, Severity.HIGH, 8,
                    "Post-signature amendments to LOA were not initialled and re-signed by the customer."),
    RejectionReason("R22", "Photo of LOA Submitted", RejectionCategory.ADMIN_ERROR, Severity.HIGH, 8,
                    "Photo submitted instead of scan or original."),
    RejectionReason("R23", "Companies House Mismatch", RejectionCategory.ADMIN_ERROR, Severity.HIGH, 8,
                    "Limited company contract details do not match Companies House records."),
    RejectionReason("R24", "DD Data Sent via Email", RejectionCategory.PROCESS_FAILURE, Severity.HIGH, 8,
                    "Direct Debit / bank account information transmitted via email."),
    RejectionReason("R25", "Unapproved DD Script Used", RejectionCategory.PROCESS_FAILURE, Severity.HIGH, 8,
                    "Agent collected bank details using a non-Watt-approved script."),
    RejectionReason("R26", "Insecure Data Transfer", RejectionCategory.PROCESS_FAILURE, Severity.HIGH, 8,
                    "Sensitive customer data not transmitted via a secured route."),
    RejectionReason("R27", "Verbal LOA Script Not Supplier-Approved", RejectionCategory.COMPLIANCE_ISSUE, Severity.HIGH, 8,
                    "Verbal LOA taken using a script that was not provided by or approved by the chosen supplier."),
)


# Index for O(1) lookup by code.
REJECTION_REASONS_BY_CODE: dict[str, RejectionReason] = {r.code: r for r in REJECTION_REASONS}


def reasons_for_category(category: RejectionCategory) -> list[RejectionReason]:
    """All detailed reasons that map to a given master category."""
    return [r for r in REJECTION_REASONS if r.category == category]


def reasons_for_standard(standard: int) -> list[RejectionReason]:
    """All detailed reasons under one of the 8 Watt Standards."""
    return [r for r in REJECTION_REASONS if r.standard == standard]


# 8 Watt Standards — the regulatory backbone.
WATT_STANDARDS: dict[int, str] = {
    1: "Identification and transparency at the start of every call.",
    2: "Respect customer's wishes; recognise and adapt for vulnerability.",
    3: "Honest, fair, and accurate sales conduct (no pressure, no misleading info).",
    4: "Customer qualification — authority, non-domestic, no prepayment meter.",
    5: "Explain principal terms before contracting.",
    6: "Confirm customer understands the transfer and all charges.",
    7: "Run the verbal script in full, accurately, with the customer answering.",
    8: "Documentation — LOA, DD data, contract — accurate, secure, and complete.",
}


# Tracker statuses (the 🔴/🟠/🟢/⚫ pipeline from the example XLSX).
class TrackerStatus(str, Enum):
    NOT_STARTED = "not_started"           # 🔴
    IN_PROGRESS = "in_progress"           # 🟠
    FIXED_RESUBMITTED = "fixed_resubmitted"  # 🟢
    LOST = "lost"                          # ⚫


class TrackerOutcome(str, Enum):
    FIXED_AND_SUBMITTED = "fixed_and_submitted"
    CUSTOMER_LOST = "customer_lost"
    CANCELLED = "cancelled"
    NOT_RECOVERABLE = "not_recoverable"


# Suppliers in scope (canonical codes; matches D-supplier-scripts.md).
class Supplier(str, Enum):
    BGL = "bgl"
    BRITISH_GAS = "british_gas"
    EDF = "edf"
    EON_NEXT = "eon_next"
    POZITIVE = "pozitive"
    SCOTTISH_POWER = "scottish_power"


SUPPLIER_LABELS: dict[Supplier, str] = {
    Supplier.BGL: "British Gas Lite (BGL)",
    Supplier.BRITISH_GAS: "British Gas",
    Supplier.EDF: "EDF",
    Supplier.EON_NEXT: "E.ON Next",
    Supplier.POZITIVE: "Pozitive Energy",
    Supplier.SCOTTISH_POWER: "Scottish Power",
}


class ScriptType(str, Enum):
    ACQUISITION = "acquisition"
    RENEWAL = "renewal"
    UPGRADE = "upgrade"
    DEEMED = "deemed"
    LOA = "loa"
    PREAMBLE = "preamble"
    AMENDMENT = "amendment"


class CallClass(str, Enum):
    GAS = "gas"
    ELEC = "elec"
    DUAL = "dual"
    NHH = "nhh"
    HH = "hh"
    ANY = "any"
