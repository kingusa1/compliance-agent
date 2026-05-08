from pathlib import Path
import pytest

from app.agent.playbooks import load_playbook, SKILLS_DIR


def test_skills_dir_exists():
    assert SKILLS_DIR.exists(), f"skills dir missing: {SKILLS_DIR}"


def test_generic_playbook_loads():
    content = load_playbook("Unknown")
    assert "Generic Compliance Playbook" in content
    assert "Universal Rules" in content


def test_exact_match_eon():
    content = load_playbook("E.ON Next")
    assert "E.ON Next" in content or "EON" in content


def test_partial_match_filename_style():
    # Supplier names sometimes arrive as "EON_Next__Elec_Verbal" from filenames
    content = load_playbook("EON_Next__Elec_Verbal")
    # Should still match the E.ON playbook
    assert "E.ON" in content or "EON" in content or "Emix" in content


def test_unknown_supplier_returns_generic():
    content = load_playbook("Martian Utilities Ltd")
    assert "Generic Compliance Playbook" in content


def test_combined_returns_supplier_plus_generic():
    from app.agent.playbooks import load_combined_playbook
    content = load_combined_playbook("British Gas")
    assert "British Gas" in content
    assert "Universal Rules" in content  # from _general.md


# ── Wave categorization-rebuild — XLSX supplier coverage extension ───────
@pytest.mark.parametrize(
    "raw_supplier, expected_marker",
    [
        ("BG lite", "British Gas"),
        ("BGL", "British Gas"),
        ("BGB", "British Gas"),
        ("BG CORE", "British Gas"),
        ("British Gas Trading Ltd", "British Gas"),
        ("E.on NEXT", "E.ON"),
        ("Eon Next", "E.ON"),
        ("Pozitive Energy Ltd", "Pozitive"),
        ("Affect Energy Ltd", "Affect Energy"),
        ("Britannia Gas", "Britannia Gas"),
        ("Smartest Energy", "Smartest Energy"),
        ("Smartestenergy Ltd", "Smartest Energy"),
        ("Total Gas and Power Ltd", "Total"),
        ("TotalEnergies Gas & Power Ltd", "Total"),
        ("United gas and Power", "United Gas"),
        ("Yu Energy", "Yu Energy"),
        ("Yu Energy Retail Ltd", "Yu Energy"),
    ],
)
def test_xlsx_supplier_aliases_resolve(raw_supplier, expected_marker):
    """Each XLSX supplier-name variant resolves to its canonical playbook."""
    content = load_playbook(raw_supplier)
    assert expected_marker in content, (
        f"{raw_supplier!r} did not load the expected playbook "
        f"(expected marker: {expected_marker!r})"
    )


def test_false_positive_short_alias_does_not_match():
    """'pigeon holdings' must NOT match the E.ON playbook just because 'eon' is a substring."""
    content = load_playbook("Pigeon Holdings")
    assert "Generic Compliance Playbook" in content  # generic fallback
    # If it had wrongly matched eon-next.md, we'd see "E.ON" or "EON" or "Emix"
    assert "Emix" not in content


def test_false_positive_edf_inside_word_does_not_match():
    """'freedfantasy ltd' must NOT match edf.md."""
    content = load_playbook("Freedfantasy Ltd")
    assert "Generic Compliance Playbook" in content


def test_longest_alias_wins():
    """When multiple aliases could match, the longer/more specific one wins."""
    from app.agent.playbooks import _find_playbook_file
    # 'british gas' and 'bgl' could both match "british gas bgl group" — longer should win
    result = _find_playbook_file("British Gas BGL Group")
    assert result is not None
    assert result.name == "british-gas.md"


def test_combined_playbook_separator_present():
    """Combined playbook must be separated by the spec's \\n\\n---\\n\\n marker."""
    from app.agent.playbooks import load_combined_playbook
    content = load_combined_playbook("E.ON Next")
    assert "\n\n---\n\n" in content


def test_combined_playbook_generic_only_has_no_separator():
    """When no supplier matches, combined returns generic alone (no leading separator)."""
    from app.agent.playbooks import load_combined_playbook
    content = load_combined_playbook("Unknown Supplier Ltd")
    assert "Generic Compliance Playbook" in content
    assert not content.startswith("\n\n---\n\n")
