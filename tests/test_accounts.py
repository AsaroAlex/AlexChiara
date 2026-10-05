"""Account security contracts, tested without providers or workspace data."""
import hashlib
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import accounts
from app.accounts import AccountError, AccountStore, OWNER_ID, SESSION_COOKIE, create_account_router


PASSWORD = "a-private-password-123"


@pytest.fixture
def store(tmp_path):
    return AccountStore(tmp_path / "accounts.sqlite3", legacy_username="filo", legacy_password="deployment-secret")


def guest(store):
    return store.new_guest_session()[0]


def signup(store, email="chiara@example.com", name="Chiara", ip="127.0.0.1"):
    return store.register(name, email, PASSWORD, guest(store), ip)


def test_registration_has_unique_member_identity_and_hashes_at_rest(store):
    cookie, session = signup(store)
    assert session["authenticated"] is True
    assert session["user"] == {"id": session["user"]["id"], "email": "chiara@example.com", "name": "Chiara", "role": "member"}
    assert len(session["user"]["id"]) == 32
    assert session["user"]["id"] != OWNER_ID
    with store.connection() as conn:
        user = conn.execute("SELECT password_hash FROM users WHERE id=?", (session["user"]["id"],)).fetchone()
        persisted = conn.execute("SELECT token_hash,csrf_token FROM account_sessions WHERE user_id=?", (session["user"]["id"],)).fetchone()
    assert user["password_hash"].startswith("scrypt$16384$8$1$")
    assert PASSWORD not in user["password_hash"]
    assert persisted["token_hash"] == hashlib.sha256(cookie.encode()).hexdigest()
    assert persisted["token_hash"] != cookie
    assert store.path.stat().st_mode & 0o777 == 0o600
    cookie2, user2 = signup(store, email="alex@example.com")
    assert user2["user"]["id"] != session["user"]["id"]
    assert cookie2 != cookie


def test_password_is_salted_per_account_and_never_trimmed(store):
    signup(store)
    signup(store, email="alex@example.com")
    with store.connection() as conn:
        values = [row[0] for row in conn.execute("SELECT password_hash FROM users WHERE role='member'")]
    assert len(set(values)) == 2
    with pytest.raises(AccountError) as exc:
        store.login("chiara@example.com", PASSWORD + " ", guest(store))
    assert exc.value.status_code == 401


def test_login_normalizes_email_and_rotates_guest_session(store):
    _, signed_up = signup(store)
    old_cookie, before = store.new_guest_session()
    new_cookie, current = store.login("  CHIARA@EXAMPLE.COM ", PASSWORD, old_cookie)
    assert current["user"]["id"] == signed_up["user"]["id"]
    assert new_cookie != old_cookie
    assert current["csrf_token"] != before["csrf_token"]
    assert store.resolve_session(old_cookie) is None
    assert store.resolve_session(new_cookie) == current
    assert not store.csrf_valid(new_cookie, before["csrf_token"])
    assert store.csrf_valid(new_cookie, current["csrf_token"])


def test_sessions_survive_store_restart_and_are_server_revocable(store):
    cookie, current = signup(store)
    restarted = AccountStore(store.path, legacy_username="filo", legacy_password="deployment-secret")
    assert restarted.resolve_session(cookie) == current
    restarted.logout(cookie)
    assert store.resolve_session(cookie) is None
    assert not store.csrf_valid(cookie, current["csrf_token"])


def test_session_expiry_is_absolute_and_prunes_expired_record(store, monkeypatch):
    cookie, current = signup(store)
    monkeypatch.setattr(accounts.time, "time", lambda: current["expires_at"] - 1)
    assert store.resolve_session(cookie) is not None
    monkeypatch.setattr(accounts.time, "time", lambda: current["expires_at"])
    assert store.resolve_session(cookie) is None
    with store.connection() as conn:
        assert conn.execute("SELECT count(*) FROM account_sessions WHERE token_hash=?", (hashlib.sha256(cookie.encode()).hexdigest(),)).fetchone()[0] == 0


def test_existing_owner_can_login_without_registration_or_password_rules(store):
    cookie, current = store.login("filo", "deployment-secret", guest(store))
    assert current["user"]["id"] == OWNER_ID
    assert current["user"]["role"] == "owner"
    assert len(store.list_users()) == 1
    assert store.resolve_session(cookie)["authenticated"] is True


def test_owner_never_assigned_to_first_registration(tmp_path):
    unconfigured = AccountStore(tmp_path / "accounts.sqlite3", legacy_password="")
    _, registered = signup(unconfigured)
    assert registered["user"]["role"] == "member"
    assert registered["user"]["id"] != OWNER_ID
    assert unconfigured.get_user(OWNER_ID) is None
    with pytest.raises(AccountError) as exc:
        unconfigured.login("filo", PASSWORD, guest(unconfigured))
    assert exc.value.status_code == 401


def test_reserved_owner_email_cannot_be_registered(tmp_path):
    reserved = AccountStore(tmp_path / "accounts.sqlite3", legacy_username="owner@example.com", legacy_password="deployment-secret")
    with pytest.raises(AccountError) as exc:
        signup(reserved, email=" OWNER@EXAMPLE.COM ")
    assert exc.value.status_code == 409
    assert reserved.list_users() == [{"id": OWNER_ID, "email": "owner@example.com", "name": "Amministratore", "role": "owner"}]


def test_owner_secret_change_revokes_owner_sessions_but_keeps_members(store):
    owner_cookie, _ = store.login("filo", "deployment-secret", guest(store))
    member_cookie, _ = signup(store)
    changed = AccountStore(store.path, legacy_username="filo", legacy_password="a-changed-owner-secret")
    assert changed.resolve_session(owner_cookie) is None
    assert changed.resolve_session(member_cookie)["authenticated"] is True
    with pytest.raises(AccountError) as exc:
        changed.login("filo", "deployment-secret", guest(changed))
    assert exc.value.status_code == 401
    assert changed.login("filo", "a-changed-owner-secret", guest(changed))[1]["user"]["id"] == OWNER_ID


def test_owner_login_disabled_when_environment_secret_removed(store):
    cookie, _ = store.login("filo", "deployment-secret", guest(store))
    disabled = AccountStore(store.path, legacy_username="filo", legacy_password="")
    assert disabled.resolve_session(cookie) is None
    with pytest.raises(AccountError) as exc:
        disabled.login("filo", "deployment-secret", guest(disabled))
    assert exc.value.status_code == 401


@pytest.mark.parametrize("password", ["", "short", "x" * 11, "x" * 129])
def test_registration_password_length_validation(store, password):
    with pytest.raises(AccountError) as exc:
        store.register("Chiara", "chiara@example.com", password, guest(store))
    assert exc.value.status_code == 422
    assert len(store.list_users()) == 1


@pytest.mark.parametrize("email", ["bad", "foo@localhost", "a..b@example.com", ".foo@example.com", "foo.@example.com", "foo@-example.com", "foo@example..com"])
def test_registration_email_validation(store, email):
    with pytest.raises(AccountError) as exc:
        store.register("Chiara", email, PASSWORD, guest(store))
    assert exc.value.status_code == 422


def test_duplicate_email_cannot_overwrite_account(store):
    _, before = signup(store)
    with pytest.raises(AccountError) as exc:
        store.register("Impostore", "CHIARA@EXAMPLE.COM", "other-password-123", guest(store))
    assert exc.value.status_code == 409
    assert store.get_user(before["user"]["id"])["name"] == "Chiara"
    assert store.login("chiara@example.com", PASSWORD, guest(store))[1]["user"] == before["user"]


def test_failed_unknown_and_known_login_have_same_error_and_do_hash_work(store, monkeypatch):
    signup(store)
    original = accounts._password_matches
    attempts = []

    def spy(password, encoded):
        attempts.append(encoded)
        return original(password, encoded)

    monkeypatch.setattr(accounts, "_password_matches", spy)
    errors = []
    for identifier in ("missing@example.com", "chiara@example.com"):
        with pytest.raises(AccountError) as exc:
            store.login(identifier, "wrong-password", guest(store))
        errors.append((exc.value.status_code, exc.value.detail))
    assert errors[0] == errors[1]
    assert len(attempts) == 2
    assert all(value.startswith("scrypt$") for value in attempts)


def test_failed_login_throttle_persists_and_expires(store, monkeypatch):
    for _ in range(8):
        with pytest.raises(AccountError) as exc:
            store.login("missing@example.com", PASSWORD, guest(store), "192.0.2.5")
        assert exc.value.status_code == 401
    restarted = AccountStore(store.path, legacy_username="filo", legacy_password="deployment-secret")
    with pytest.raises(AccountError) as exc:
        restarted.login("missing@example.com", PASSWORD, guest(restarted), "192.0.2.5")
    assert exc.value.status_code == 429
    now = accounts.time.time()
    monkeypatch.setattr(accounts.time, "time", lambda: now + 601)
    with pytest.raises(AccountError) as exc:
        restarted.login("missing@example.com", PASSWORD, guest(restarted), "192.0.2.5")
    assert exc.value.status_code == 401


def test_login_ip_limit_prevents_identifier_spraying(store):
    for index in range(60):
        with pytest.raises(AccountError) as exc:
            store.login(f"unknown-{index}@example.com", PASSWORD, guest(store), "192.0.2.9")
        assert exc.value.status_code == 401
    with pytest.raises(AccountError) as exc:
        store.login("filo", "deployment-secret", guest(store), "192.0.2.9")
    assert exc.value.status_code == 429


def test_registration_throttle_prevents_bulk_accounts(store):
    for index in range(12):
        signup(store, f"member-{index}@example.com", ip="192.0.2.12")
    with pytest.raises(AccountError) as exc:
        signup(store, "one-too-many@example.com", ip="192.0.2.12")
    assert exc.value.status_code == 429


@pytest.mark.parametrize("value", [None, "", "forged", "x" * 100000, "a" * 43])
def test_forged_sessions_cannot_authenticate_or_authorize(store, value):
    assert store.resolve_session(value) is None
    assert store.csrf_valid(value, "a" * 43) is False


@pytest.fixture
def client(store):
    app = FastAPI()
    app.include_router(create_account_router(store))
    with TestClient(app, base_url="https://testserver") as browser:
        yield browser


def csrf_header(client):
    return {"X-CSRF-Token": client.get("/api/auth/session").json()["csrf_token"]}


def test_auth_route_cookie_flags_no_native_challenge_or_password_payload(client):
    initial = client.get("/api/auth/session")
    assert initial.json()["authenticated"] is False
    assert initial.headers["cache-control"] == "no-store"
    cookie = initial.headers["set-cookie"]
    assert "HttpOnly" in cookie and "Secure" in cookie and "SameSite=lax" in cookie and "Path=/" in cookie
    response = client.post("/api/auth/login", json={"identifier": "filo", "password": "deployment-secret"}, headers={"X-CSRF-Token": initial.json()["csrf_token"]})
    assert response.status_code == 200
    assert response.json()["user"]["role"] == "owner"
    assert "www-authenticate" not in response.headers
    assert "deployment-secret" not in response.text
    assert "password_hash" not in response.text


def test_login_accepts_frontend_email_field_and_rejects_ambiguous_keys(client):
    headers = csrf_header(client)
    response = client.post("/api/auth/login", json={"email": "filo", "password": "deployment-secret"}, headers=headers)
    assert response.status_code == 200
    assert response.json()["user"]["id"] == OWNER_ID
    response = client.post("/api/auth/login", json={"email": "filo", "identifier": "other", "password": "deployment-secret"}, headers={"X-CSRF-Token": response.json()["csrf_token"]})
    assert response.status_code == 422


def test_auth_mutations_require_guest_csrf_and_same_origin(client):
    headers = csrf_header(client)
    payload = {"identifier": "filo", "password": "deployment-secret"}
    assert client.post("/api/auth/login", json=payload).status_code == 403
    assert client.post("/api/auth/login", json=payload, headers={"X-CSRF-Token": "forged"}).status_code == 403
    assert client.post("/api/auth/login", json=payload, headers={**headers, "Origin": "https://evil.example"}).status_code == 403
    assert client.post("/api/auth/login", json=payload, headers={**headers, "Origin": "https://testserver"}).status_code == 200


def test_signup_and_logout_rotation_revoke_authenticated_cookie(client, store):
    old_cookie = client.get("/api/auth/session").cookies[SESSION_COOKIE]
    headers = csrf_header(client)
    registered = client.post("/api/auth/register", json={"name": "Chiara", "email": "chiara@example.com", "password": PASSWORD}, headers=headers)
    assert registered.status_code == 200
    cookie = client.cookies[SESSION_COOKIE]
    assert cookie != old_cookie
    assert store.resolve_session(old_cookie) is None
    assert client.post("/api/auth/logout", headers=headers).status_code == 403
    assert client.post("/api/auth/logout", headers={"X-CSRF-Token": registered.json()["csrf_token"]}).status_code == 200
    assert store.resolve_session(cookie) is None
    assert client.get("/api/auth/session").json()["authenticated"] is False


def test_account_api_rejects_role_or_id_assignment(client, store):
    response = client.post("/api/auth/register", json={"name": "Attacker", "email": "attacker@example.com", "password": PASSWORD, "role": "owner", "id": "owner"}, headers=csrf_header(client))
    assert response.status_code == 422
    assert len(store.list_users()) == 1


def test_authenticated_user_must_log_out_before_registering_again(client):
    current = client.post("/api/auth/login", json={"identifier": "filo", "password": "deployment-secret"}, headers=csrf_header(client)).json()
    response = client.post("/api/auth/register", json={"name": "Chiara", "email": "chiara@example.com", "password": PASSWORD}, headers={"X-CSRF-Token": current["csrf_token"]})
    assert response.status_code == 409
    assert client.get("/api/auth/session").json()["user"]["id"] == OWNER_ID


def test_successful_logins_do_not_exhaust_the_failed_attempt_budget(store):
    signup(store)
    for _ in range(12):
        store.login("chiara@example.com", PASSWORD, guest(store), "10.0.0.1")
    for _ in range(8):
        with pytest.raises(AccountError) as exc:
            store.login("chiara@example.com", "wrong-password-123", guest(store), "10.0.0.1")
        assert exc.value.status_code == 401
    with pytest.raises(AccountError) as exc:
        store.login("chiara@example.com", PASSWORD, guest(store), "10.0.0.1")
    assert exc.value.status_code == 429


def test_throttle_uses_the_configured_proxy_header_not_forwarded_for(tmp_path, monkeypatch):
    monkeypatch.setenv("FILO_CLIENT_IP_HEADER", "X-Real-IP")
    store = AccountStore(tmp_path / "accounts.sqlite3", legacy_username="filo", legacy_password="deployment-secret")
    app = FastAPI()
    app.include_router(create_account_router(store))
    with TestClient(app) as client:
        def attempt(forwarded):
            csrf = client.get("/api/auth/session").json()["csrf_token"]
            return client.post("/api/auth/login", json={"email": "filo", "password": "not-the-secret"},
                               headers={"X-CSRF-Token": csrf, "X-Real-IP": "203.0.113.9", "X-Forwarded-For": forwarded}).status_code
        statuses = [attempt(f"198.51.100.{index}") for index in range(10)]
    assert statuses[:8] == [401] * 8
    assert statuses[8:] == [429, 429]


def test_invalid_proxy_header_falls_back_to_the_socket_address(monkeypatch):
    monkeypatch.setenv("FILO_CLIENT_IP_HEADER", "X-Real-IP")

    class Request:
        headers = {"X-Real-IP": "not-an-ip"}

        class client:
            host = "192.0.2.1"
    assert accounts.client_address(Request()) == "192.0.2.1"
    Request.headers = {"X-Real-IP": " 2001:db8::1 "}
    assert accounts.client_address(Request()) == "2001:db8::1"
    monkeypatch.delenv("FILO_CLIENT_IP_HEADER")
    assert accounts.client_address(Request()) == "192.0.2.1"


def test_owner_username_matching_a_member_disables_owner_login_without_crashing(tmp_path):
    path = tmp_path / "accounts.sqlite3"
    first = AccountStore(path, legacy_username="filo", legacy_password="deployment-secret")
    _, member = first.register("Chiara", "chiara@example.com", PASSWORD, first.new_guest_session()[0], "127.0.0.1")
    with first.connection() as conn:
        conn.execute("DELETE FROM users WHERE id=?", (OWNER_ID,))
    renamed = AccountStore(path, legacy_username="chiara@example.com", legacy_password="deployment-secret")
    assert renamed.owner_enabled is False
    _, session = renamed.login("chiara@example.com", PASSWORD, renamed.new_guest_session()[0])
    assert session["user"]["id"] == member["user"]["id"]
    with pytest.raises(AccountError):
        renamed.login("chiara@example.com", "deployment-secret", renamed.new_guest_session()[0])


def test_renamed_owner_keeps_the_workspace_and_releases_the_old_name(tmp_path):
    path = tmp_path / "accounts.sqlite3"
    AccountStore(path, legacy_username="filo", legacy_password="deployment-secret")
    renamed = AccountStore(path, legacy_username="titolare", legacy_password="deployment-secret")
    _, session = renamed.login("titolare", "deployment-secret", renamed.new_guest_session()[0])
    assert session["user"]["id"] == OWNER_ID
    assert session["user"]["email"] == "titolare"
    with pytest.raises(AccountError):
        renamed.login("filo", "deployment-secret", renamed.new_guest_session()[0])
