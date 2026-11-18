"""A software passkey for the tests: it answers WebAuthn options the way a phone or Touch ID would.
Protected (human-owned). Uses cbor2 + cryptography, which the webauthn package installs anyway.

    auth = SoftAuthenticator(origin="http://localhost:8090")
    cred = auth.create(options_json)            # the browser's answer to registration options
    assertion = auth.get(options_json)           # the browser's answer to sign-in options
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import struct

import cbor2
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec


def b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def unb64u(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


class SoftAuthenticator:
    def __init__(self, origin: str) -> None:
        self.origin = origin
        self.creds: dict[bytes, dict] = {}       # credential id -> {key, rp_id, user_handle, count}

    def _client_data(self, kind: str, challenge: str, origin: str | None = None) -> bytes:
        return json.dumps({"type": kind, "challenge": challenge, "origin": origin or self.origin,
                           "crossOrigin": False}).encode()

    def create(self, options_json: str, origin: str | None = None) -> dict:
        o = json.loads(options_json)
        rp_id = o["rp"]["id"]
        key = ec.generate_private_key(ec.SECP256R1())
        nums = key.public_key().public_numbers()
        cose = cbor2.dumps({1: 2, 3: -7, -1: 1, -2: nums.x.to_bytes(32, "big"), -3: nums.y.to_bytes(32, "big")})
        cred_id = os.urandom(16)
        self.creds[cred_id] = {"key": key, "rp_id": rp_id, "user_handle": o["user"]["id"], "count": 0}
        auth_data = (hashlib.sha256(rp_id.encode()).digest() + bytes([0x45]) + struct.pack(">I", 0)
                     + b"\x00" * 16 + struct.pack(">H", len(cred_id)) + cred_id + cose)
        att = cbor2.dumps({"fmt": "none", "attStmt": {}, "authData": auth_data})
        cd = self._client_data("webauthn.create", o["challenge"], origin)
        return {"id": b64u(cred_id), "rawId": b64u(cred_id), "type": "public-key",
                "response": {"clientDataJSON": b64u(cd), "attestationObject": b64u(att), "transports": ["internal"]},
                "clientExtensionResults": {}, "authenticatorAttachment": "platform"}

    def get(self, options_json: str, cred_id: bytes | None = None, origin: str | None = None,
            tamper: bool = False) -> dict:
        o = json.loads(options_json)
        cred_id = cred_id or next(iter(self.creds))
        c = self.creds[cred_id]
        c["count"] += 1
        auth_data = hashlib.sha256(o["rpId"].encode()).digest() + bytes([0x05]) + struct.pack(">I", c["count"])
        cd = self._client_data("webauthn.get", o["challenge"], origin)
        sig = c["key"].sign(auth_data + hashlib.sha256(cd).digest(), ec.ECDSA(hashes.SHA256()))
        if tamper:
            sig = sig[:-1] + bytes([sig[-1] ^ 1])
        return {"id": b64u(cred_id), "rawId": b64u(cred_id), "type": "public-key",
                "response": {"clientDataJSON": b64u(cd), "authenticatorData": b64u(auth_data),
                             "signature": b64u(sig), "userHandle": c["user_handle"]},
                "clientExtensionResults": {}, "authenticatorAttachment": "platform"}
