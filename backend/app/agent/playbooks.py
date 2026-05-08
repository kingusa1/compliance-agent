"""Supplier-specific compliance playbook loader.

Playbooks are markdown files at backend/skills/*.md. The file named
_general.md is the fallback when no supplier-specific playbook exists
or when the supplier cannot be matched.

Matching strategy:
  1. Normalize supplier name (lowercase, strip punctuation)
  2. Check for exact file match: skills/<normalized>.md
  3. Check if any known supplier token appears in the input
  4. Fall back to _general.md
"""
from pathlib import Path
import re

SKILLS_DIR = Path(__file__).parent.parent.parent / "skills"

# Map of supplier tokens → playbook filename (without .md). XLSX shows ops
# uses 33 distinct supplier-name spellings (BG lite / BGL / BG CORE /
# E.on Next / Pozitive Energy Ltd / Yu Energy Retail Ltd / etc); each
# alias here funnels them onto a canonical playbook slug.
_SUPPLIER_ALIASES = {
    # E.ON Next family
    "eon": "eon-next",
    "eon next": "eon-next",
    "e.on": "eon-next",
    "e on": "eon-next",
    "e on next": "eon-next",
    "emix": "eon-next",
    # British Gas family
    "british gas": "british-gas",
    "britishgas": "british-gas",
    "bgl": "british-gas",
    "bgb": "british-gas",
    "bg lite": "british-gas",
    "bg core": "british-gas",
    "bgcore": "british-gas",
    "british gas lite": "british-gas",
    "british gas core": "british-gas",
    "british gas business": "british-gas",
    "british gas trading": "british-gas",
    # EDF
    "edf": "edf",
    # Pozitive
    "pozitive": "pozitive",
    "pozitive energy": "pozitive",
    # Scottish Power
    "scottish power": "scottish-power",
    "scottishpower": "scottish-power",
    "sp business": "scottish-power",
    # Suppliers added Wave-categorization-rebuild — playbook files are
    # skeletons today; refine as data accumulates.
    "affect": "affect",
    "affect energy": "affect",
    "britannia": "britannia",
    "britannia gas": "britannia",
    "smartest": "smartest",
    "smartest energy": "smartest",
    "smartestenergy": "smartest",
    "total gas": "total",
    "total gas and power": "total",
    "totalenergies": "total",
    "total energies": "total",
    "united gas": "united",
    "united gas and power": "united",
    "yu energy": "yu-energy",
    "yu": "yu-energy",
}


def _normalize(supplier: str) -> str:
    """Lowercase, replace punctuation and separators with spaces, collapse whitespace."""
    s = supplier.lower()
    s = re.sub(r"[._\-/]+", " ", s)
    s = re.sub(r"[^\w\s]", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _find_playbook_file(supplier: str) -> Path | None:
    """Return the Path to the most specific matching playbook, or None.

    Single-word aliases (eon, edf, bgl, ...) match only as whole tokens to
    avoid false positives (e.g., "eon" should NOT match "pigeon holdings").
    Multi-word aliases (british gas, eon next, ...) match as phrase
    containment. Longest alias wins when multiple match.
    """
    if not supplier:
        return None
    normalized_phrase = _normalize(supplier)
    normalized_tokens = set(normalized_phrase.split())

    # Sort longest-alias-first so "british gas" wins over "bgl" when both match
    for alias, filename in sorted(_SUPPLIER_ALIASES.items(), key=lambda kv: -len(kv[0])):
        norm_alias = _normalize(alias)
        if " " in norm_alias:
            hit = norm_alias in normalized_phrase
        else:
            hit = norm_alias in normalized_tokens
        if hit:
            candidate = SKILLS_DIR / f"{filename}.md"
            if candidate.exists():
                return candidate
    return None


def load_playbook(supplier: str) -> str:
    """Load the best-matching playbook for a supplier. Falls back to _general.md."""
    specific = _find_playbook_file(supplier)
    if specific:
        return specific.read_text(encoding="utf-8")

    generic = SKILLS_DIR / "_general.md"
    if generic.exists():
        return generic.read_text(encoding="utf-8")
    return ""


def load_combined_playbook(supplier: str) -> str:
    """Return supplier playbook + generic playbook joined. Used for agent system prompt."""
    specific = _find_playbook_file(supplier)
    generic_path = SKILLS_DIR / "_general.md"
    generic = generic_path.read_text(encoding="utf-8") if generic_path.exists() else ""

    if specific:
        return specific.read_text(encoding="utf-8") + "\n\n---\n\n" + generic
    return generic
