"""Passkeys (WebAuthn) for Dewdrop (chat/SPEC.md §10), on top of the `webauthn` package.

Fill in the functions marked TODO. Tests: chat/tests/test_step_passkeys.py
The package does the cryptography; these functions only pass the right values in and out.
"""
from __future__ import annotations

import json
from typing import Any

from webauthn import (
    generate_authentication_options,
    generate_registration_options,
    options_to_json,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers import base64url_to_bytes, bytes_to_base64url
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)
from webauthn.helpers.cose import COSEAlgorithmIdentifier

RP_NAME = "Dewdrop"
ALGORITHMS = [COSEAlgorithmIdentifier.ECDSA_SHA_256, COSEAlgorithmIdentifier.RSASSA_PKCS1_v1_5_SHA_256]


class PasskeyError(Exception):
    """Verification failed (the route turns it into 400 or 401)."""


def user_handle(username: str) -> bytes:
    """TODO (task P02). The WebAuthn user handle: the username encoded as UTF-8."""
    return username.encode("utf-8")


def registration_options(settings: Any, username: str, existing: list[bytes]) -> tuple[str, bytes]:
    """Options for adding a passkey. Returns (options as a JSON string, the challenge bytes).

    TODO (task P02). Call generate_registration_options(
        rp_id=settings.RP_ID, rp_name=RP_NAME, user_id=user_handle(username), user_name=username,
        user_display_name=username,
        exclude_credentials=[PublicKeyCredentialDescriptor(id=c) for c in existing],
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.REQUIRED,
            user_verification=UserVerificationRequirement.REQUIRED),
        supported_pub_key_algs=ALGORITHMS)
    and return (options_to_json(opts), opts.challenge).
    """
    opts = generate_registration_options(
        rp_id=settings.RP_ID, rp_name=RP_NAME, user_id=user_handle(username), user_name=username,
        user_display_name=username,
        exclude_credentials=[PublicKeyCredentialDescriptor(id=c) for c in existing],
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.REQUIRED,
            user_verification=UserVerificationRequirement.REQUIRED),
        supported_pub_key_algs=ALGORITHMS)
    return (options_to_json(opts), opts.challenge)


def verify_registration(settings: Any, credential: dict, challenge: bytes) -> tuple[bytes, bytes, int]:
    """Check the browser's answer to the registration options.

    TODO (task P03). Call verify_registration_response(credential=credential,
    expected_challenge=challenge, expected_rp_id=settings.RP_ID, expected_origin=settings.ORIGIN,
    require_user_verification=True). Return (v.credential_id, v.credential_public_key, v.sign_count).
    Any exception from the package -> raise PasskeyError(str(e)).
    """
    try:
        v = verify_registration_response(
            credential=credential,
            expected_challenge=challenge,
            expected_rp_id=settings.RP_ID,
            expected_origin=settings.ORIGIN,
            require_user_verification=True,
        )
        return (v.credential_id, v.credential_public_key, v.sign_count)
    except Exception as e:
        raise PasskeyError(str(e))


def authentication_options(settings: Any) -> tuple[str, bytes]:
    """Options for signing in with any passkey of this site. Returns (JSON string, challenge).

    TODO (task P04). generate_authentication_options(rp_id=settings.RP_ID, allow_credentials=[],
    user_verification=UserVerificationRequirement.REQUIRED); return (options_to_json(o), o.challenge).
    """
    o = generate_authentication_options(rp_id=settings.RP_ID, allow_credentials=[],
        user_verification=UserVerificationRequirement.REQUIRED)
    return (options_to_json(o), o.challenge)


def credential_id(credential: dict) -> bytes:
    """TODO (task P04). The credential id the browser sent: base64url_to_bytes(credential["rawId"])
    (fall back to credential["id"]); a missing or malformed value -> raise PasskeyError."""
    try:
        return base64url_to_bytes(credential["rawId"])
    except (KeyError, TypeError, ValueError):
        try:
            return base64url_to_bytes(credential["id"])
        except (KeyError, TypeError, ValueError):
            raise PasskeyError("missing or malformed credential id")


def verify_authentication(settings: Any, credential: dict, challenge: bytes, public_key: bytes,
                          sign_count: int) -> int:
    """Check a sign-in. Returns the new signature counter.

    TODO (task P05). Call verify_authentication_response(credential=credential,
    expected_challenge=challenge, expected_rp_id=settings.RP_ID, expected_origin=settings.ORIGIN,
    credential_public_key=public_key, credential_current_sign_count=sign_count,
    require_user_verification=True) and return v.new_sign_count.
    Any exception -> raise PasskeyError(str(e)).
    """
    try:
        v = verify_authentication_response(
            credential=credential,
            expected_challenge=challenge,
            expected_rp_id=settings.RP_ID,
            expected_origin=settings.ORIGIN,
            credential_public_key=public_key,
            credential_current_sign_count=sign_count,
            require_user_verification=True,
        )
        return v.new_sign_count
    except Exception as e:
        raise PasskeyError(str(e))


def _unused() -> None:  # imports you may need
    json.dumps({})
    bytes_to_base64url(b"")
