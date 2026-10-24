import pytest
from fastapi.testclient import TestClient
import server as srv

client = TestClient(srv.app)
SECRET = srv.__dict__  # noqa: F841 — segredo lido do auth.py via env


def _secret():
    from auth import LAB_SECRET
    return LAB_SECRET


def test_auth_invalid_secret():
    r = client.post("/api/auth", json={"device": "x", "secret": "errado"})
    assert r.status_code == 403


def test_auth_valid():
    r = client.post("/api/auth", json={"device": "x", "secret": _secret()})
    assert r.status_code == 200
    assert "token" in r.json()


def test_location_requires_token():
    r = client.post("/api/location",
                    json={"device": "x", "lat": 1, "lon": 1, "accuracy": 1})
    assert r.status_code == 401


def test_location_with_token():
    token = client.post("/api/auth",
                        json={"device": "x", "secret": _secret()}).json()["token"]
    r = client.post("/api/location",
                    headers={"Authorization": f"Bearer {token}"},
                    json={"device": "x", "lat": 38.7, "lon": -9.1, "accuracy": 5})
    assert r.status_code == 200


def test_location_rejects_bad_lat():
    token = client.post("/api/auth",
                        json={"device": "x", "secret": _secret()}).json()["token"]
    r = client.post("/api/location",
                    headers={"Authorization": f"Bearer {token}"},
                    json={"device": "x", "lat": 95.0, "lon": -9.1, "accuracy": 5})
    assert r.status_code == 422
