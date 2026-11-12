"""The README's integration example runs as written."""
import importlib

from fastapi.testclient import TestClient


def test_the_example_app_wires_up(tmp_path, monkeypatch):
    monkeypatch.setenv("NOTIFY_DATABASE_URL", f"sqlite:///{tmp_path / 'demo.db'}")
    monkeypatch.setenv("NOTIFY_EMAIL_TRANSPORT", "memory")
    module = importlib.import_module("examples.fastapi_app")
    module = importlib.reload(module)
    with TestClient(module.app) as client:
        scheduled = client.post("/api/v1/demo/letters/42/remind", headers={"X-User": "u-1"}).json()
        assert scheduled["schedule"] == "at 3 set time(s) (Asia/Kolkata)"
        assert client.get("/api/v1/demo/notifications/reminders",
                          headers={"X-User": "u-1"}).json()[0]["status"] == "active"
        assert client.post("/api/v1/demo/letters/42/replied").json() == {"cancelled": True}
