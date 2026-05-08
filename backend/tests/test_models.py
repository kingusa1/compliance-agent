import uuid
from datetime import datetime

from app.models import Call, CallCheckpoint


def test_create_call(test_db):
    call = Call(
        id=str(uuid.uuid4()),
        filename="test.mp3",
        file_path="/uploads/test.mp3",
        file_size=1024,
        status="processing",
    )
    test_db.add(call)
    test_db.commit()

    result = test_db.query(Call).first()
    assert result.filename == "test.mp3"
    assert result.status == "processing"
    assert result.compliant is None
    assert isinstance(result.created_at, datetime)


def test_update_call_with_results(test_db):
    call_id = str(uuid.uuid4())
    call = Call(
        id=call_id,
        filename="test.mp3",
        file_path="/uploads/test.mp3",
        file_size=1024,
        status="processing",
    )
    test_db.add(call)
    test_db.commit()

    call.status = "completed"
    call.compliant = False
    call.reason = "Agent did not disclose third-party status"
    call.excerpt = "We work with all major suppliers"
    call.transcript = "Full transcript here"
    call.completed_at = datetime.utcnow()
    test_db.commit()

    result = test_db.query(Call).filter_by(id=call_id).first()
    assert result.status == "completed"
    assert result.compliant is False
    assert result.reason == "Agent did not disclose third-party status"


def test_call_checkpoint_relationship(test_db):
    call_id = str(uuid.uuid4())
    call = Call(
        id=call_id,
        filename="test.mp3",
        file_path="/uploads/test.mp3",
        file_size=1024,
        status="completed",
    )
    test_db.add(call)
    test_db.commit()

    cp1 = CallCheckpoint(
        call_id=call_id,
        rule_text="Agent states company is a third party",
        passed=True,
        excerpt="we are a third party",
    )
    cp2 = CallCheckpoint(
        call_id=call_id,
        rule_text="Agent states company is NOT an energy supplier",
        passed=False,
        excerpt="We work with all major suppliers",
    )
    test_db.add_all([cp1, cp2])
    test_db.commit()

    checkpoints = test_db.query(CallCheckpoint).filter_by(call_id=call_id).all()
    assert len(checkpoints) == 2
    assert checkpoints[0].passed is True
    assert checkpoints[1].passed is False

    # Verify relationship
    result = test_db.query(Call).filter_by(id=call_id).first()
    assert len(result.checkpoints) == 2
