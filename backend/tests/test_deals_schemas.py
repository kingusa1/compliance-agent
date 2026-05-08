import uuid
from datetime import datetime
from app.schemas import CustomerDealCreate, CustomerDealOut


def test_create_schema_accepts_minimum_fields():
    payload = CustomerDealCreate(customer_name="Acme Ltd")
    assert payload.customer_name == "Acme Ltd"
    assert payload.status == "in_progress"  # default
    assert payload.risk_tags == []


def test_out_schema_roundtrips_uuid():
    did = uuid.uuid4()
    out = CustomerDealOut(
        id=did,
        customer_name="Acme Ltd",
        created_at=datetime.now(),
        status="in_progress",
        risk_tags=[],
    )
    assert out.id == did
    assert out.final_score is None


def test_assigned_agent_id_is_string_not_uuid():
    # profiles.id is varchar in this schema, so this must accept plain strings.
    payload = CustomerDealCreate(customer_name="X", assigned_agent_id="user-abc-123")
    assert payload.assigned_agent_id == "user-abc-123"
