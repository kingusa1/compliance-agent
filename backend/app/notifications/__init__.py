"""Outbound notifications — feedback email after analysis, agent
escalation alerts, etc. Each module exposes a small async function the
Inngest workflows call so notification failure can't take the pipeline
down.
"""
