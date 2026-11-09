"""Password hashing with Argon2id (OWASP's recommended algorithm)."""

from contextlib import suppress

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

_hasher = PasswordHasher()

# Verified when a login names an unknown user, so response time does not reveal
# whether the account exists.
_DUMMY_HASH = _hasher.hash("dummy-password-for-timing-equalization")

MIN_PASSWORD_LENGTH = 12
MAX_PASSWORD_LENGTH = 128


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, hashed: str | None) -> bool:
    if not hashed:
        _burn_time(password)
        return False
    try:
        return _hasher.verify(hashed, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(hashed: str) -> bool:
    return _hasher.check_needs_rehash(hashed)


def _burn_time(password: str) -> None:
    with suppress(VerificationError):
        _hasher.verify(_DUMMY_HASH, password)
