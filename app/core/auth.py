import hashlib
import hmac
import math
import secrets
import time

from app.core.cache import TTLCache
from app.core.errors import AuthRateLimitedError

SESSION_COOKIE = "brasil_lens_session"
SESSION_SECONDS = 30 * 24 * 60 * 60
WINDOW_SECONDS = 300
ACCOUNT_LIMIT = 10
IP_LIMIT = 30
EMAIL_LIMIT = 100
# Cada chave guarda (tentativas, fim da janela): a janela é fixa, então insistir não a prolonga.
_attempts: TTLCache[tuple[int, float]] = TTLCache(ttl_seconds=WINDOW_SECONDS, max_entries=20_000)


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


def _account_key(email: str, ip: str | None) -> str:
    return f"account:{hashlib.sha256(email.encode()).hexdigest()}:{ip}"


def check_auth_attempt(email: str, ip: str | None = None) -> None:
    # Conta+IP: quem erra a senha de outra pessoa só tranca a si mesmo. O IP limita quem varre
    # e-mails (e o scrypt do cadastro); o e-mail sozinho é o teto contra um ataque distribuído.
    # O IP vem do cabeçalho do proxy: forjá-lo escapa do limite por IP, mas não dos outros dois.
    limits = [(_account_key(email, ip), ACCOUNT_LIMIT)]
    limits.append((f"email:{hashlib.sha256(email.encode()).hexdigest()}", EMAIL_LIMIT))
    if ip is not None:
        limits.append((f"ip:{ip}", IP_LIMIT))
    now = time.monotonic()
    entries = []
    for key, limit in limits:
        attempts, until = _attempts.get(key) or (0, now + WINDOW_SECONDS)
        if attempts >= limit:
            raise AuthRateLimitedError(
                "Muitas tentativas. Aguarde 5 minutos para tentar novamente."
            )
        entries.append((key, attempts + 1, until))
    for key, attempts, until in entries:
        _attempts.set(key, (attempts, until), ttl_seconds=max(1, math.ceil(until - now)))


def reset_state() -> None:
    _attempts.clear()


def clear_auth_attempts(email: str, ip: str | None = None) -> None:
    key = _account_key(email, ip)
    _attempts.set(key, (0, time.monotonic() + WINDOW_SECONDS))
