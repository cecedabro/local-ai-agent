"""Small judge for chat/coder/passkeys.py (tasks P02–P05). Protected."""
from __future__ import annotations

import json

import pytest

from coder import passkeys
from soft_authenticator import SoftAuthenticator, b64u

ORIGIN = "http://localhost:8090"


class S:
    RP_ID = "localhost"
    ORIGIN = ORIGIN


# ---------------------------------------------------------------- P02
def test_registration_options():
    assert passkeys.user_handle("lisa") == b"lisa"
    js, challenge = passkeys.registration_options(S(), "lisa", [b"\x01\x02"])
    o = json.loads(js)
    assert o["rp"] == {"id": "localhost", "name": "Dewdrop"}
    assert o["user"]["id"] == b64u(b"lisa") and o["user"]["name"] == "lisa"
    assert o["challenge"] == b64u(challenge) and len(challenge) >= 16
    assert o["authenticatorSelection"]["residentKey"] == "required"
    assert o["authenticatorSelection"]["userVerification"] == "required"
    assert [c["id"] for c in o["excludeCredentials"]] == [b64u(b"\x01\x02")]


# ---------------------------------------------------------------- P03
def test_verify_registration():
    js, challenge = passkeys.registration_options(S(), "lisa", [])
    auth = SoftAuthenticator(ORIGIN)
    cred_id, public_key, count = passkeys.verify_registration(S(), auth.create(js), challenge)
    assert cred_id in auth.creds and isinstance(public_key, bytes) and count == 0
    with pytest.raises(passkeys.PasskeyError):
        passkeys.verify_registration(S(), auth.create(js, origin="https://evil.example"), challenge)
    with pytest.raises(passkeys.PasskeyError):
        passkeys.verify_registration(S(), auth.create(js), b"another challenge 123")


# ---------------------------------------------------------------- P04
def test_authentication_options_and_credential_id():
    js, challenge = passkeys.authentication_options(S())
    o = json.loads(js)
    assert o["rpId"] == "localhost" and o["challenge"] == b64u(challenge)
    assert o.get("allowCredentials", []) == [] and o["userVerification"] == "required"
    assert passkeys.credential_id({"rawId": b64u(b"abc"), "id": "x"}) == b"abc"
    assert passkeys.credential_id({"id": b64u(b"xyz")}) == b"xyz"
    with pytest.raises(passkeys.PasskeyError):
        passkeys.credential_id({})


# ---------------------------------------------------------------- P05
def test_verify_authentication():
    auth = SoftAuthenticator(ORIGIN)
    rjs, rch = passkeys.registration_options(S(), "lisa", [])
    cred_id, public_key, count = passkeys.verify_registration(S(), auth.create(rjs), rch)
    ajs, ach = passkeys.authentication_options(S())
    assert passkeys.verify_authentication(S(), auth.get(ajs), ach, public_key, count) == 1
    ajs, ach = passkeys.authentication_options(S())
    with pytest.raises(passkeys.PasskeyError):
        passkeys.verify_authentication(S(), auth.get(ajs, tamper=True), ach, public_key, 1)
    ajs, ach = passkeys.authentication_options(S())
    with pytest.raises(passkeys.PasskeyError):
        passkeys.verify_authentication(S(), auth.get(ajs, origin="https://evil.example"), ach, public_key, 1)


# ---------------------------------------------------------------- P01
def test_db_and_settings(tmp_path):
    from coder.config import Settings
    from coder.db import DB
    s = Settings({"RP_ID": "dewdrop.example", "ORIGIN": "https://dewdrop.example"})
    assert (s.RP_ID, s.ORIGIN) == ("dewdrop.example", "https://dewdrop.example")
    assert (Settings({}).RP_ID, Settings({}).ORIGIN) == ("localhost", "http://localhost:8090")
    db = DB(str(tmp_path))
    db.init_schema()
    db.create_user("lisa", "x", ["chat"])
    pk = db.add_passkey("lisa", b"cred-1", b"pub", 0, "Mac")
    assert pk["name"] == "Mac" and pk["id"]
    assert [p["name"] for p in db.list_passkeys("lisa")] == ["Mac"]
    row = db.get_passkey(b"cred-1")
    assert row["username"] == "lisa" and bytes(row["public_key"]) == b"pub" and row["sign_count"] == 0
    db.touch_passkey(pk["id"], 5)
    row = db.get_passkey(b"cred-1")
    assert row["sign_count"] == 5 and row["last_used"]
    assert db.delete_passkey(pk["id"], "someone-else") is False
    db.put_challenge("h1", "login", None, b"chal")
    assert db.take_challenge("h1", "register") is None, "wrong kind"
    assert db.take_challenge("h1", "login") is None, "a failed take still deletes it"
    db.put_challenge("h2", "register", "lisa", b"chal2")
    assert db.take_challenge("h2", "register") == (b"chal2", "lisa")
    assert db.take_challenge("h2", "register") is None, "single use"
    db.put_challenge("h3", "login", None, b"old", ttl=-1)
    assert db.take_challenge("h3", "login") is None, "expired"
    db.delete_user("lisa")
    assert db.get_passkey(b"cred-1") is None
    db.close()
