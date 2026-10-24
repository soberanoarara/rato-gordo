from location_manager import LocationStore


def test_upsert_overwrites_ts_and_keeps_history():
    s = LocationStore(history_max=3)
    for i in range(5):
        s.upsert({"device": "d1", "lat": 38.0 + i, "lon": -9.0, "accuracy": 5})
    assert len(s.history["d1"]) == 3          # circular respeitado
    assert s.last["d1"]["lat"] == 38.0 + 4    # última posição
    assert "ts" in s.last["d1"]


def test_subscribe_replays_last_state():
    s = LocationStore()
    s.upsert({"device": "d1", "lat": 38.0, "lon": -9.0, "accuracy": 5})
    q = s.subscribe()
    data = q.get_nowait()
    assert data["device"] == "d1"
