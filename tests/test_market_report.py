"""Product strategy is readable by the site owner, not public registrants."""
from fastapi.testclient import TestClient

from app.main import create_app


def test_market_report_is_owner_only_and_not_cached(tmp_path, monkeypatch):
    monkeypatch.setenv("FILO_ACCESS_USERNAME", "filo")
    monkeypatch.setenv("FILO_ACCESS_PASSWORD", "owner-report-test-password")
    monkeypatch.setenv("FILO_REAL_DATA_ONLY", "1")
    app = create_app(data_dir=tmp_path, start_worker=False)
    with TestClient(app) as client:
        anonymous = client.get("/api/research/market-review")
        assert anonymous.status_code == 401
        assert anonymous.headers["Cache-Control"] == "no-store"
        csrf = client.get("/api/auth/session").json()["csrf_token"]
        login = client.post("/api/auth/login", json={"email": "filo", "password": "owner-report-test-password"}, headers={"X-CSRF-Token": csrf})
        assert login.status_code == 200
        report = client.get("/api/research/market-review")
        assert report.status_code == 200
        assert report.headers["Content-Type"].startswith("text/html")
        assert report.headers["Cache-Control"] == "no-store"
        assert "Fattura24" in report.text and "19 prodotti" in report.text
    with TestClient(app) as member:
        csrf = member.get("/api/auth/session").json()["csrf_token"]
        registration = member.post("/api/auth/register", json={"name": "Studio test", "email": "report-member@example.test", "password": "synthetic-report-password"}, headers={"X-CSRF-Token": csrf})
        assert registration.status_code == 200
        forbidden = member.get("/api/research/market-review")
        assert forbidden.status_code == 403
        assert "Fattura24" not in forbidden.text
