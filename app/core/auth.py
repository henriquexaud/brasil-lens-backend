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
# Bloqueio progressivo por e-mail: é o único teto imune a forjar o IP, então é a defesa real
# contra força bruta vertical (muitas senhas num e-mail). Conta só falhas de senha; a contagem
# vive por horas para o bloqueio escalar entre janelas. Depois da franquia, cada falha tranca o
# e-mail pelo tempo da escada (6ª falha: 1 min; 7ª: 5 min; 8ª: 15 min; 9ª+: 60 min).
EMAIL_FAILURE_TTL = 24 * 60 * 60
EMAIL_FREE_FAILURES = 5
_EMAIL_LOCK_LADDER = (60, 300, 900, 3600)
_RATE_LIMITED_MESSAGE = "Muitas tentativas de acesso. Tente novamente mais tarde."
# Contadores de tentativa (conta+IP, IP) guardam (tentativas, fim da janela): a janela é fixa,
# então insistir não a prolonga. O bloqueio por e-mail guarda (falhas, trancado até).
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


def _email_key(email: str) -> str:
    return f"email-fail:{hashlib.sha256(email.encode()).hexdigest()}"


def check_auth_attempt(email: str, ip: str | None = None) -> None:
    # Conta+IP: quem erra a senha de outra pessoa só tranca a si mesmo. O IP limita quem varre
    # e-mails e o custo do scrypt por janela; forjá-lo escapa dos dois. O bloqueio por e-mail,
    # imune a isso, é verificado antes do scrypt e é o teto real contra força bruta vertical.
    now = time.monotonic()
    _, locked_until = _attempts.get(_email_key(email)) or (0, 0.0)
    if locked_until > now:
        raise AuthRateLimitedError(_RATE_LIMITED_MESSAGE)
    limits = [(_account_key(email, ip), ACCOUNT_LIMIT)]
    if ip is not None:
        limits.append((f"ip:{ip}", IP_LIMIT))
    entries = []
    for key, limit in limits:
        attempts, until = _attempts.get(key) or (0, now + WINDOW_SECONDS)
        if attempts >= limit:
            raise AuthRateLimitedError(_RATE_LIMITED_MESSAGE)
        entries.append((key, attempts + 1, until))
    for key, attempts, until in entries:
        _attempts.set(key, (attempts, until), ttl_seconds=max(1, math.ceil(until - now)))


def record_auth_failure(email: str) -> None:
    # Só em falha de senha (login/exclusão), nunca em cadastro de e-mail já existente: senão
    # bastaria tentar cadastrar o e-mail de alguém para trancar o acesso da pessoa. Cada falha
    # além da franquia estende o bloqueio pela escada, até o teto.
    now = time.monotonic()
    failures, _ = _attempts.get(_email_key(email)) or (0, 0.0)
    failures += 1
    over = failures - EMAIL_FREE_FAILURES
    lock = _EMAIL_LOCK_LADDER[min(over, len(_EMAIL_LOCK_LADDER)) - 1] if over > 0 else 0
    _attempts.set(_email_key(email), (failures, now + lock), ttl_seconds=EMAIL_FAILURE_TTL)


def reset_state() -> None:
    _attempts.clear()


def clear_auth_attempts(email: str, ip: str | None = None) -> None:
    # Sucesso perdoa a conta+IP e zera o bloqueio progressivo do e-mail (erros de digitação).
    _attempts.set(_account_key(email, ip), (0, time.monotonic() + WINDOW_SECONDS))
    _attempts.set(_email_key(email), (0, 0.0), ttl_seconds=1)
