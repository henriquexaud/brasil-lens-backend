import hashlib
import hmac
import secrets

from app.core.cache import TTLCache
from app.core.errors import AuthRateLimitedError

SESSION_COOKIE = "brasil_lens_session"
SESSION_SECONDS = 30 * 24 * 60 * 60
_attempts: TTLCache[int] = TTLCache(ttl_seconds=300, max_entries=2048)


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    derived = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=5, dklen=32)
    return f"scrypt${salt.hex()}${derived.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, salt, expected = encoded.split("$")
        if algorithm != "scrypt":
            return False
        derived = hashlib.scrypt(
            password.encode(), salt=bytes.fromhex(salt), n=2**14, r=8, p=5, dklen=32
        )
        return hmac.compare_digest(derived, bytes.fromhex(expected))
    except (ValueError, TypeError):
        return False


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def check_auth_attempt(email: str) -> None:
    key = hashlib.sha256(email.encode()).hexdigest()
    attempts = _attempts.get(key) or 0
    if attempts >= 10:
        raise AuthRateLimitedError("Muitas tentativas. Aguarde 5 minutos para tentar novamente.")
    _attempts.set(key, attempts + 1)


def reset_state() -> None:
    _attempts.clear()


def clear_auth_attempts(email: str) -> None:
    _attempts.set(hashlib.sha256(email.encode()).hexdigest(), 0)
