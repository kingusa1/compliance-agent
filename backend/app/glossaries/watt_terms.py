"""Base UK energy-industry glossary used as the AssemblyAI word_boost floor.

Every transcription job receives this list regardless of whether the
supplier is known. Per L9 contract design_decisions.flag_1_word_boost:
boost biases — does not force — the model, so over-inclusion is safe.
"""

WATT_BASE_TERMS: list[str] = [
    # Watt brand
    "Watt Utilities", "Watt",
    # Letter-of-Authority + commercial billing acronyms heard on calls
    "LOA", "Letter of Authority",
    "MOP", "DCP", "DDI", "BACS",
    # Meter point identifiers (digit strings — boost helps with framing words)
    "MPAN", "MPRN",
    # Units + regulators
    "kVA", "kWh", "OFGEM", "ombudsman", "Ofgas",
    # Compliance vocabulary the checkpoint analyzer scores against
    "verbal contract", "cooling-off period", "direct debit",
    "supply number", "meter point", "annual cost",
    "renewal", "termination", "credit check",
]
