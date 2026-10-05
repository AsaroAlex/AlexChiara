"""Reusable company identity stays private, persistent and explicitly entered."""
from fastapi.testclient import TestClient
import pytest

from app.company import COMPANY_FIELDS, OPTIONAL_COMPANY_FIELDS, get_document_company
from app.db import Database
from app.main import create_app


PASSWORD = "Private#Company-42"
PROFILE = {
    "name": "  Studio Aurora  ", "sector": " Consulenza ",
    "description": "Servizi per imprese\nConsulenza organizzativa",
    "signature": "Marta\nStudio Aurora", "legal_name": " Aurora Srl ",
    "vat_number": " it12345678901 ", "tax_code": "rssmra80a01h501u",
    "address": " Via del Lavoro 12 ", "postal_code": "00100", "city": " Roma ",
    "province": "rm", "email": "INFO@EXAMPLE.TEST", "phone": "+39 06 1234567",
    "pec": "AURORA@PEC.EXAMPLE.TEST", "sdi_code": "abc1234",
}
NORMALIZED_PROFILE = {
    **PROFILE, "name": "Studio Aurora", "sector": "Consulenza", "legal_name": "Aurora Srl",
    "vat_number": "12345678901", "tax_code": "RSSMRA80A01H501U",
    "address": "Via del Lavoro 12", "city": "Roma", "province": "RM",
    "email": "info@example.test", "pec": "aurora@pec.example.test", "sdi_code": "ABC1234",
}


@pytest.fixture
def site(tmp_path, monkeypatch):
    monkeypatch.setenv("FILO_ACCESS_PASSWORD", "owner-password-company")
    monkeypatch.setenv("FILO_ACCESS_USERNAME", "filo")
    monkeypatch.setenv("FILO_REAL_DATA_ONLY", "1")
    monkeypatch.setenv("FILO_AI_ENABLED", "0")
    app = create_app(data_dir=tmp_path, start_worker=False)
    with TestClient(app) as client:
        yield app, client, tmp_path


def register(client, email):
    csrf = client.get("/api/auth/session").json()["csrf_token"]
    response = client.post(
        "/api/auth/register", json={"name": "Studio privato", "email": email, "password": PASSWORD},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 200, response.text
    return response.json()["csrf_token"]


def save(client, csrf, profile):
    return client.put("/api/company", json=profile, headers={"X-CSRF-Token": csrf})


def private_db(app, client):
    user_id = client.get("/api/auth/session").json()["user"]["id"]
    client.get("/api/bootstrap")
    return app.state.workspaces[user_id].state.db


def test_new_profile_fields_round_trip_and_survive_restart(site):
    app, client, directory = site
    csrf = register(client, "profile@example.test")
    state = client.get("/api/bootstrap").json()
    assert all(state["company"][field] == "" for field in COMPANY_FIELDS)
    response = save(client, csrf, PROFILE)
    assert response.status_code == 200, response.text
    assert response.json()["company"] == {**NORMALIZED_PROFILE, "demo": False}
    assert client.get("/api/bootstrap").json()["company"] == {**NORMALIZED_PROFILE, "demo": False}
    assert get_document_company(private_db(app, client)) == NORMALIZED_PROFILE

    restarted = create_app(data_dir=directory, start_worker=False)
    with TestClient(restarted) as returning:
        returning.cookies.update(client.cookies)
        assert returning.get("/api/bootstrap").json()["company"] == {**NORMALIZED_PROFILE, "demo": False}
        assert get_document_company(private_db(restarted, returning)) == NORMALIZED_PROFILE


def test_old_client_put_preserves_new_fields_and_explicit_blank_clears(site):
    app, client, _ = site
    csrf = register(client, "legacy-client@example.test")
    assert save(client, csrf, PROFILE).status_code == 200
    response = save(client, csrf, {"name": "Studio aggiornato", "signature": "Nuova firma"})
    assert response.status_code == 200, response.text
    current = response.json()["company"]
    assert current["name"] == "Studio aggiornato" and current["signature"] == "Nuova firma"
    for field in OPTIONAL_COMPANY_FIELDS:
        assert current[field] == NORMALIZED_PROFILE[field]
    cleared = save(client, csrf, {"name": "Studio aggiornato", "vat_number": "", "email": ""})
    assert cleared.status_code == 200
    assert cleared.json()["company"]["vat_number"] == ""
    assert cleared.json()["company"]["email"] == ""
    assert cleared.json()["company"]["pec"] == NORMALIZED_PROFILE["pec"]
    assert get_document_company(private_db(app, client))["vat_number"] == ""


def test_legacy_saved_profile_needs_no_migration_or_fictional_defaults(tmp_path):
    db = Database(tmp_path / "legacy.sqlite3")
    assert get_document_company(db) == dict.fromkeys(COMPANY_FIELDS, "")
    db.set_setting("company", {"name": "Studio esistente", "signature": "Firma esistente", "demo": False})
    current = get_document_company(db)
    assert current["name"] == "Studio esistente" and current["signature"] == "Firma esistente"
    assert all(current[field] == "" for field in OPTIONAL_COMPANY_FIELDS)
    db.set_setting("company", {**PROFILE, "demo": True})
    assert get_document_company(db) == dict.fromkeys(COMPANY_FIELDS, "")


@pytest.mark.parametrize("field,value", [
    ("vat_number", "1234567890"), ("vat_number", "FR12345678901"),
    ("vat_number", "IT12345X78901"), ("vat_number", "１２３４５６７８９０１"),
    ("tax_code", "ABC123"), ("tax_code", "RSSMRA80A01H501!"),
    ("postal_code", "1000"), ("postal_code", "00A00"),
    ("province", "ROM"), ("province", "R1"),
    ("sdi_code", "ABC123"), ("sdi_code", "ABC123!"),
    ("email", "info@example"), ("pec", "not-an-email"),
    ("email", "info..team@example.test"), ("email", "info@example..test"),
    ("name", "Studio\x00Aurora"), ("address", "Via Roma\n12"),
    ("legal_name", "Aurora\u202eSrl"), ("signature", "Firma\x1b[31m"),
    ("description", "Profilo\x7f"), ("name", "   "),
    ("vat_number", 12345678901), ("phone", None),
])
def test_invalid_company_data_is_rejected_without_overwriting_saved_profile(site, field, value):
    _, client, _ = site
    csrf = register(client, "invalid-company@example.test")
    assert save(client, csrf, PROFILE).status_code == 200
    response = save(client, csrf, {"name": "Studio Aurora", field: value})
    assert response.status_code == 422, response.text
    assert client.get("/api/bootstrap").json()["company"] == {**NORMALIZED_PROFILE, "demo": False}


def test_structural_validation_accepts_company_tax_code_and_special_sdi_code(site):
    _, client, _ = site
    csrf = register(client, "structural@example.test")
    response = save(client, csrf, {"name": "Studio", "tax_code": "12345678901", "sdi_code": "0000000"})
    assert response.status_code == 200, response.text
    assert response.json()["company"]["tax_code"] == "12345678901"
    # This is a formatting check, not a public-register or SDI verification.
    assert response.json()["company"]["sdi_code"] == "0000000"


def test_literal_markup_is_text_and_corrupt_legacy_values_never_break_documents(tmp_path):
    db = Database(tmp_path / "literal.sqlite3")
    db.set_setting("company", {
        "name": "Studio <script>alert(1)</script>", "demo": False,
        "vat_number": "not a VAT", "address": "Address\x00control", "email": "bad email",
    })
    current = get_document_company(db)
    assert current["name"] == "Studio <script>alert(1)</script>"
    assert current["vat_number"] == current["address"] == current["email"] == ""
    db.set_setting("company", "invalid legacy shape")
    assert get_document_company(db) == dict.fromkeys(COMPANY_FIELDS, "")


def test_company_identity_stays_private_between_accounts_and_owner(site):
    app, alice, _ = site
    alice_csrf = register(alice, "company-alice@example.test")
    with TestClient(app) as bob:
        bob_csrf = register(bob, "company-bob@example.test")
        assert save(alice, alice_csrf, PROFILE).status_code == 200
        bob_profile = {"name": "Studio Bob", "vat_number": "98765432109", "address": "Via Bob 3"}
        assert save(bob, bob_csrf, bob_profile).status_code == 200
        alice_identity = get_document_company(private_db(app, alice))
        bob_identity = get_document_company(private_db(app, bob))
        assert alice_identity == NORMALIZED_PROFILE
        assert bob_identity["name"] == "Studio Bob" and bob_identity["vat_number"] == "98765432109"
        assert bob_identity["address"] == "Via Bob 3" and bob_identity["legal_name"] == ""
        assert "Aurora" not in str(bob_identity)
        assert save(bob, alice_csrf, {"name": "Intrusione"}).status_code == 403
        assert bob.get("/api/bootstrap").json()["company"]["name"] == "Studio Bob"
        assert get_document_company(app.state.db) == dict.fromkeys(COMPANY_FIELDS, "")


def test_company_mutations_remain_authenticated_and_require_csrf(site):
    _, client, _ = site
    assert client.put("/api/company", json=PROFILE).status_code == 401
    register(client, "company-csrf@example.test")
    assert client.put("/api/company", json=PROFILE).status_code == 403
    assert client.get("/api/bootstrap").json()["company"]["vat_number"] == ""
