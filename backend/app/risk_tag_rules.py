"""Risk-tag mapping for Pillar 2 (L2) flag derivation.

Watt's reviewer dashboard groups failed checkpoints into 4 risk buckets
(per digest §9): ombudsman, mis-selling, complaint, cancellation. The
mapping is keyed on (rule_category, severity) so a single failed
checkpoint can be routed to the right downstream queue without the UI
having to know about every individual rule_id.

Categories live on rules_catalog.json — keep this map in sync when adding
new categories. Severity is one of `critical | high | medium`; medium-tier
checkpoints don't get a risk_tag (they're coaching, not customer-impacting).
"""

# (rule_category, severity) -> Watt risk_tag
RISK_TAG_MAP: dict[tuple[str, str], str] = {
    ("identity",   "critical"): "mis-selling",
    ("identity",   "high"):     "complaint",
    ("disclosure", "critical"): "ombudsman",
    ("disclosure", "high"):     "complaint",
    ("terms",      "critical"): "mis-selling",
    ("terms",      "high"):     "complaint",
    ("consent",    "critical"): "cancellation",
    ("consent",    "high"):     "cancellation",
    ("pricing",    "critical"): "ombudsman",
    ("pricing",    "high"):     "mis-selling",
}
