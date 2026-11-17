"""Password hashing with argon2id (SPEC §2)."""
from __future__ import annotations
import argon2


_ph = argon2.PasswordHasher(time_cost=2, memory_cost=65536, parallelism=1, hash_len=32, salt_len=16)


def hash_password(password: str) -> str:
    return _ph.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    try:
        _ph.verify(hashed, password)
        return True
    except argon2.exceptions.VerifyMismatchError:
        return False
