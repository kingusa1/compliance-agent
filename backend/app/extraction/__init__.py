"""Pillar 2 — Data extraction subpackage.

Decomposes a finalized call into three structured tables (call_segments,
flags, extracted_entities) reviewers and the v2 dashboards can filter on.
Stage vocabulary is locked to Watt's 6-stage taxonomy (intro, qualification,
pitch, transfer, verbal, close); see `.planning/enterprise-sprint/L2-data-extraction.json`.
"""
