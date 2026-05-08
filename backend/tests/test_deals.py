from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def test_create_and_get_deal():
    r = client.post("/api/deals", json={"customer_name": "Acme Ltd", "supplier": "E.ON"})
    assert r.status_code == 201, r.text
    body = r.json()
    deal_id = body["id"]
    assert body["customer_name"] == "Acme Ltd"
    assert body["status"] == "in_progress"
    assert body["supplier"] == "E.ON"

    r2 = client.get(f"/api/deals/{deal_id}")
    assert r2.status_code == 200
    assert r2.json()["id"] == deal_id


def test_list_deals_filters_by_status():
    client.post("/api/deals", json={"customer_name": "Foo", "status": "closed"})
    r = client.get("/api/deals?status=closed")
    assert r.status_code == 200
    rows = r.json()["deals"]
    assert all(d["status"] == "closed" for d in rows)


def test_get_deal_calls_returns_empty_for_new_deal():
    r = client.post("/api/deals", json={"customer_name": "Bar"})
    deal_id = r.json()["id"]
    r2 = client.get(f"/api/deals/{deal_id}/calls")
    assert r2.status_code == 200
    assert r2.json() == {"calls": []}


def test_get_deal_404_when_not_found():
    import uuid
    r = client.get(f"/api/deals/{uuid.uuid4()}")
    assert r.status_code == 404
