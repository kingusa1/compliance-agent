"""Tests for the `ReviewerEdit` audit-log model (Phase C task C1).

`reviewer_edits` captures every inline edit made on /tracker — field name,
old value, new value, reviewer id, timestamp. Used by the Phase C UI to
render "Last edited by" lines and the AI-badge tooltip's "Previously AI: X"
text.

Note: ``ReviewerEdit`` is imported at module scope so its ``__tablename__``
is registered with ``Base.metadata`` *before* the ``test_db`` fixture calls
``Base.metadata.create_all``. Importing inside the test would create the
SQLite schema without our table.
"""
from app.models import ReviewerEdit


def test_reviewer_edit_persists(test_db):
    e = ReviewerEdit(
        rejection_id="00000000-0000-0000-0000-000000000001",
        field="supplier",
        old_value="E.ON Next",
        new_value="British Gas",
        reviewer_id="rev-1",
    )
    test_db.add(e)
    test_db.commit()
    rows = test_db.query(ReviewerEdit).all()
    assert len(rows) == 1
    assert rows[0].field == "supplier"
    assert rows[0].at is not None
