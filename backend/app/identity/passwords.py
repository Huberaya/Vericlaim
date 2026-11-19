"""Password hashing and policy for self-service accounts (chantier C14).

Design notes, because password handling is where a "self-service" feature usually
loses its security:

* **scrypt from the standard library**, not a bespoke construction. Parameters are
  stored *inside* the hash string, so a future increase can be rolled out without
  invalidating existing accounts. No extra dependency is added to the runtime.
* **Constant-time comparison** on the derived key.
* The policy is published to the caller verbatim (`password_policy_errors`), so a
  refusal is never a mystery, and the frontend can pre-validate with the same
  rules.
* Nothing here logs, stores or returns a password. A password never reaches the
  audit trail.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import unicodedata

#: scrypt parameters. n=2**14 keeps a single hash around ~40 ms on a laptop while
#: staying well inside the memory budget of a small instance. The values are
#: stored with the hash, so they can be raised later without breaking accounts.
SCRYPT_N = 2**14
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_DKLEN = 32
SALT_BYTES = 16
HASH_SCHEME = "scrypt"

MIN_PASSWORD_LENGTH = 12
MIN_EMAIL_TOKEN_LENGTH = 4
_MULTI_SEPARATOR_PATTERN = re.compile(r"[^a-zA-Z0-9]+")
MAX_PASSWORD_LENGTH = 256

#: Short list of the passwords that actually appear at the top of every breach
#: corpus. Deliberately small and readable: a long list would give a false sense
#: of coverage, and the length requirement is doing the real work.
COMMON_PASSWORDS = frozenset(
    {
        "password",
        "password1",
        "motdepasse",
        "mot de passe",
        "azertyuiop",
        "qwertyuiop",
        "123456789012",
        "000000000000",
        "vericlaim",
        "vericlaim2026",
        "letmein123456",
        "administrateur",
        "welcome12345",
        "changeme1234",
    }
)


def hash_password(password: str) -> str:
    """Return ``scrypt$n$r$p$salt$key``, all parameters self-describing."""
    if not isinstance(password, str) or not password:
        raise ValueError("Mot de passe vide.")
    salt = secrets.token_bytes(SALT_BYTES)
    derived = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=SCRYPT_N,
        r=SCRYPT_R,
        p=SCRYPT_P,
        dklen=SCRYPT_DKLEN,
    )
    return f"{HASH_SCHEME}${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${salt.hex()}${derived.hex()}"


def verify_password(password: str, stored_hash: str | None) -> bool:
    """Constant-time verification. A missing hash always fails."""
    if not stored_hash or not isinstance(password, str):
        return False
    try:
        scheme, n, r, p, salt_hex, key_hex = stored_hash.split("$")
        if scheme != HASH_SCHEME:
            return False
        derived = hashlib.scrypt(
            password.encode("utf-8"),
            salt=bytes.fromhex(salt_hex),
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(bytes.fromhex(key_hex)),
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(derived, bytes.fromhex(key_hex))


def _normalized(value: str) -> str:
    return unicodedata.normalize("NFKD", value).casefold()


def password_policy_errors(password: str, *, email: str | None = None) -> list[str]:
    """Every reason the password is refused, in plain French, none of them secret.

    Returning the whole list (rather than the first failure) lets a form explain
    itself in one round trip instead of one error per attempt.
    """
    errors: list[str] = []
    if len(password) < MIN_PASSWORD_LENGTH:
        errors.append(f"Le mot de passe doit contenir au moins {MIN_PASSWORD_LENGTH} caractères.")
    if len(password) > MAX_PASSWORD_LENGTH:
        errors.append(f"Le mot de passe ne peut pas dépasser {MAX_PASSWORD_LENGTH} caractères.")
    if password.strip() != password or not password.strip():
        errors.append("Le mot de passe ne peut pas commencer ou finir par un espace, ni être vide.")
    if _normalized(password) in {_normalized(item) for item in COMMON_PASSWORDS}:
        errors.append("Ce mot de passe figure parmi les plus utilisés : choisissez-en un autre.")
    if email:
        local_part = email.split("@", 1)[0].strip()
        normalized_password = _normalized(password)
        # The whole local part, but also its meaningful tokens: "Jean-Dupont-2026!"
        # must be refused for jean.dupont@…, which the whole-string test alone
        # misses. Tokens shorter than 4 characters are ignored — they would match
        # by accident and block legitimate passwords.
        candidates = [_normalized(local_part)] + [
            _normalized(token)
            for token in _MULTI_SEPARATOR_PATTERN.split(local_part)
            if len(token) >= MIN_EMAIL_TOKEN_LENGTH
        ]
        if any(candidate and candidate in normalized_password for candidate in candidates):
            errors.append("Le mot de passe ne doit pas contenir votre adresse e-mail.")
    if not any(character.isdigit() for character in password):
        errors.append("Le mot de passe doit contenir au moins un chiffre.")
    if not any(character.isalpha() for character in password):
        errors.append("Le mot de passe doit contenir au moins une lettre.")
    return errors


def generate_token() -> str:
    """A 256-bit URL-safe token. Only its digest is stored."""
    return secrets.token_urlsafe(32)


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
