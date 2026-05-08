-- Phase J Task 32 — prompt version analytics
--
-- Reference queries for the `prompt_version` column added to verdict_history
-- by alembic revision 4d5d09ce7455. Paste into psql / a BI tool / a cron job.
--
-- The version is a 12-char sha256 of the supplier playbook + static prompt
-- table, computed by app.prompts.version_for_supplier. Every prompt edit
-- produces a new version — so "override rate by version" surfaces prompt
-- regressions the moment a new prompt ships.

-- ─── 1. Override rate by prompt version ─────────────────────────────────────
-- How often do reviewers flip the AI's answer for each prompt revision?
-- Rising override rate on a new version = that prompt edit regressed quality.
SELECT
    ai.prompt_version,
    COUNT(*)                                           AS ai_verdicts,
    COUNT(DISTINCT ai.call_id)                         AS calls_affected,
    SUM(CASE WHEN rev.verdict IS DISTINCT FROM ai.verdict
             THEN 1 ELSE 0 END)                        AS overrides,
    ROUND(
        100.0 * SUM(CASE WHEN rev.verdict IS DISTINCT FROM ai.verdict
                          THEN 1 ELSE 0 END)
             / NULLIF(COUNT(*), 0),
        2
    )                                                  AS override_pct
FROM verdict_history ai
LEFT JOIN verdict_history rev
  ON rev.call_id       = ai.call_id
 AND rev.checkpoint_id = ai.checkpoint_id
 AND rev.actor_type   IN ('reviewer', 'lead')
 AND rev.is_current    = true
WHERE ai.actor_type = 'ai'
GROUP BY ai.prompt_version
ORDER BY ai_verdicts DESC;


-- ─── 2. Which prompt versions are "in production" right now? ────────────────
-- Useful after shipping a prompt edit: confirm new verdicts are picking up
-- the new hash.
SELECT
    prompt_version,
    COUNT(*)                     AS rows,
    MIN(created_at)              AS first_seen,
    MAX(created_at)              AS last_seen
FROM verdict_history
WHERE actor_type = 'ai'
  AND prompt_version IS NOT NULL
GROUP BY prompt_version
ORDER BY last_seen DESC;


-- ─── 3. Overrides of a specific version (drill-down) ────────────────────────
-- Replace :version with the 12-char hash you want to investigate.
-- SELECT
--     ai.call_id,
--     ai.checkpoint_id,
--     ai.verdict      AS ai_verdict,
--     rev.verdict     AS reviewer_verdict,
--     rev.reasoning   AS reviewer_reasoning,
--     rev.actor_id    AS reviewer_id,
--     rev.created_at  AS overridden_at
-- FROM verdict_history ai
-- JOIN verdict_history rev
--   ON rev.call_id       = ai.call_id
--  AND rev.checkpoint_id = ai.checkpoint_id
--  AND rev.actor_type   IN ('reviewer', 'lead')
--  AND rev.is_current    = true
-- WHERE ai.actor_type     = 'ai'
--   AND ai.prompt_version = :version
--   AND rev.verdict IS DISTINCT FROM ai.verdict
-- ORDER BY rev.created_at DESC;
