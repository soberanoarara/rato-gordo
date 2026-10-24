"""Regressão CR1: /api/devices deve incluir seriais vistos pelo adb,
mesmo sem sessão de vídeo iniciada.

Antes do fix: registry vazio → lista vazia.
Depois do fix: adb devices injeta entradas com managed=False.
"""
import pytest
from fastapi.testclient import TestClient

from server import app
from auth import issue_token


def _auth_header() -> dict:
    result = issue_token("panel", "manifesty1-lab")
    assert result, "issue_token falhou — segredo do lab mudou?"
    token, _ = result
    return {"Authorization": f"Bearer {token}"}


def test_devices_endpoint_includes_adb_serials(monkeypatch):
    fake = ["FAKE0001", "FAKE0002"]

    async def _fake_enumerate():
        return fake

    monkeypatch.setattr("server._enumerate_adb_devices", _fake_enumerate)

    with TestClient(app) as client:
        r = client.get("/api/devices", headers=_auth_header())
        assert r.status_code == 200
        payload = r.json()
        serials = {d["serial"] for d in payload["devices"]}
        assert "FAKE0001" in serials
        assert "FAKE0002" in serials
        for d in payload["devices"]:
            if d["serial"] in fake:
                assert d["managed"] is False
                assert d["video"] is False
                assert d["control"] is False


def test_devices_endpoint_requires_auth():
    with TestClient(app) as client:
        r = client.get("/api/devices")
        assert r.status_code == 401
