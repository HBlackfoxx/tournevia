"""Named accounts and opaque sessions; no client-selected organization or role."""
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import secrets
import threading
from typing import Literal
from uuid import UUID

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError, InvalidHashError
from fastapi import Request, Security
from fastapi.security import APIKeyCookie
from pydantic import ConfigDict, Field, SecretStr, StrictBool, field_validator
from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session

from app.accounts import User, LoginSession, LoginThrottle
from app.planning import Model
from app.records import DataError, lock_owner

COOKIE = '__Host-vrp_session'
LIFETIME = timedelta(hours=8)
IDLE = timedelta(minutes=30)
HASHER = PasswordHasher()  # Argon2id, library's RFC 9106 low-memory profile.
DUMMY_HASH = HASHER.hash(secrets.token_urlsafe(32))
SESSION_COOKIE = APIKeyCookie(name=COOKIE, auto_error=False, description='Required in shared mode. Obtain through POST /session; writes also require X-CSRF-Token and the configured Origin.')
LOCAL_COOKIE = 'vrp_local_session'
LOCAL_SESSION_COOKIE = APIKeyCookie(name=LOCAL_COOKIE, scheme_name='LocalSessionCookie', auto_error=False,
    description='Authenticated loopback local mode only; shared mode never accepts this cookie.')


def cookie_name(settings) -> str:
    return LOCAL_COOKIE if settings.app_mode == 'local' else COOKIE


def set_session_cookie(response, settings, token):
    response.set_cookie(cookie_name(settings), token, max_age=int(LIFETIME.total_seconds()), path='/',
                        secure=settings.app_mode != 'local', httponly=True, samesite='strict')


class UserRecord(Model):
    id: UUID
    organization_id: UUID
    username: str
    display_name: str
    role: Literal['admin', 'planner']
    active: bool


class Identity(Model):
    organization_id: UUID
    user_id: UUID | None
    username: str
    display_name: str
    role: Literal['admin', 'planner']
    language: Literal['en', 'fr'] = 'en'

    @property
    def actor(self) -> str:
        return str(self.user_id) if self.user_id else 'local-demo'


class SessionRecord(Model):
    identity: Identity
    csrf_token: str | None
    expires_at: datetime | None


class LoginRequest(Model):
    model_config = ConfigDict(hide_input_in_errors=True)
    username: str = Field(min_length=1, max_length=80, pattern=r'^[a-zA-Z0-9][a-zA-Z0-9_.@+-]*$')
    password: SecretStr = Field(min_length=1, max_length=128)

    @field_validator('username')
    @classmethod
    def normalized(cls, value: str) -> str:
        return value.lower()


class CreateUser(LoginRequest):
    password: SecretStr = Field(min_length=15, max_length=128)
    display_name: str = Field(min_length=1, max_length=120)
    role: Literal['admin', 'planner'] = 'planner'
    language: Literal['en', 'fr'] = Field('en', description='Interface language of the new account.')


class UpdateUser(Model):
    display_name: str = Field(min_length=1, max_length=120)
    role: Literal['admin', 'planner']
    active: StrictBool


class PasswordChange(Model):
    model_config = ConfigDict(hide_input_in_errors=True)
    current_password: SecretStr = Field(min_length=1, max_length=128)
    new_password: SecretStr = Field(min_length=15, max_length=128)


class PasswordReset(Model):
    model_config = ConfigDict(hide_input_in_errors=True)
    new_password: SecretStr = Field(min_length=15, max_length=128)


class Preferences(Model):
    language: Literal['en', 'fr']


def update_preferences(session: Session, actor: Identity, data: Preferences) -> Identity:
    user = session.scalar(select(User).where(User.id == actor.user_id,
        User.organization_id == actor.organization_id, User.active.is_(True)).with_for_update())
    if user is None:
        raise DataError(401, 'SESSION_REQUIRED', 'Sign in to continue.')
    user.language = data.language
    session.flush()
    return identity(user)


def stamp() -> datetime:
    return datetime.now(timezone.utc)


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def csrf(token: str) -> str:
    return hmac.new(token.encode(), b'vrp-csrf-v1', hashlib.sha256).hexdigest()


def identity(user: User) -> Identity:
    return Identity(organization_id=user.organization_id, user_id=user.id, username=user.username,
                    display_name=user.display_name, role=user.role, language=user.language)


def public_user(user: User) -> UserRecord:
    return UserRecord.model_validate({k: getattr(user, k) for k in UserRecord.model_fields})


def require_admin(who: Identity) -> None:
    if who.role != 'admin':
        raise DataError(403, 'ADMIN_REQUIRED', 'Only an administrator can manage depot settings and users.')


def require_current_admin(session: Session, who: Identity) -> None:
    """Under the owner lock: the acting account is still an active admin. Its session was
    resolved in an earlier transaction, which another admin may have disabled or demoted since."""
    if who.user_id is None or session.scalar(select(User.id).where(
            User.id == who.user_id, User.organization_id == who.organization_id,
            User.active.is_(True), User.role == 'admin')) is None:
        raise DataError(403, 'ADMIN_REQUIRED', 'Only an administrator can manage depot settings and users.')


def create_user(session: Session, owner: UUID, data: CreateUser) -> UserRecord:
    lock_owner(session, owner)
    # Serialize installation-wide usernames across the two-company test boundary.
    session.execute(text('SELECT pg_advisory_xact_lock(73645102)'))
    if session.scalar(select(User.id).where(User.username == data.username)):
        raise DataError(409, 'USERNAME_UNAVAILABLE', 'Choose a different username.')
    user = User(organization_id=owner, username=data.username, display_name=data.display_name,
                password_hash=HASHER.hash(data.password.get_secret_value()), role=data.role, active=True,
                language=data.language)
    session.add(user); session.flush()
    return public_user(user)


def update_user(session: Session, who: Identity, key: UUID, data: UpdateUser) -> UserRecord:
    require_admin(who); lock_owner(session, who.organization_id); require_current_admin(session, who)
    user = session.scalar(select(User).where(User.id == key, User.organization_id == who.organization_id).with_for_update())
    if user is None:
        raise DataError(404, 'NOT_FOUND', 'User not found in this organization.')
    if user.id == who.user_id and (not data.active or data.role != 'admin'):
        raise DataError(409, 'SELF_LOCKOUT', 'Another administrator must disable or demote your account.')
    if user.active and user.role == 'admin' and (not data.active or data.role != 'admin'):
        admins = list(session.scalars(select(User.id).where(User.organization_id == who.organization_id,
                                                          User.active.is_(True), User.role == 'admin')))
        if len(admins) < 2:
            raise DataError(409, 'LAST_ADMIN', 'Keep at least one active administrator in this company.')
    user.display_name, user.role, user.active = data.display_name, data.role, data.active
    session.execute(delete(LoginSession).where(LoginSession.user_id == user.id))
    session.flush()
    return public_user(user)


# Failed sign-ins only. One client cannot lock a username for everyone: its own
# (username, client) bucket closes long before the per-username bucket, which bounds
# distributed guessing. There is deliberately no installation-wide bucket: checked
# before the password, it let a handful of addresses spraying usernames lock every
# user out. Shared HTTPS also rate-limits sign-in per address in nginx.
THROTTLES = (('client', 5, timedelta(minutes=15)), ('username', 50, timedelta(minutes=15)))
LOGIN_LOCK = 73645103
# Each Argon2 verification uses 64 MiB. Bound concurrent hashing in this process
# instead of hashing while holding the sign-in lock and a pooled connection.
HASHING = threading.BoundedSemaphore(2)


def login_client(request: Request) -> str:
    """Throttle identity of the caller; never used for authorization.

    Only an operator-declared proxy that overwrites X-VRP-Client with the connecting
    address (the bundled nginx does) is trusted; otherwise the direct peer address is used.
    """
    settings = request.app.state.settings
    if settings.app_mode == 'shared' and settings.trust_proxy_client_header:
        forwarded = request.headers.get('x-vrp-client', '')
        if 0 < len(forwarded) <= 64:
            return forwarded
    return request.client.host if request.client else 'unknown'


def throttle_keys(username: str, client: str) -> dict[str, str]:
    # Bounded counters survive API restarts. Client addresses are only stored hashed.
    return {'client': digest(f'client|{client}|{username}'), 'username': digest(username)}


def lock_sign_in(session: Session, keys: dict[str, str]) -> None:
    """Serialize counter updates, purge expired counters and refuse a throttled caller."""
    session.execute(text('SELECT pg_advisory_xact_lock(:key)'), {'key': LOGIN_LOCK})
    session.execute(delete(LoginThrottle).where(LoginThrottle.expires_at <= stamp()))
    for name, limit, _ in THROTTLES:
        bucket = session.get(LoginThrottle, keys[name])
        if bucket is not None and bucket.attempts >= limit:
            raise DataError(429, 'LOGIN_THROTTLED', 'Sign-in temporarily limited. Try again later.')


def stored_credentials(session: Session, data: LoginRequest, client: str) -> str | None:
    """First sign-in step: refuse a throttled caller and read the hash of an active user, if any."""
    lock_sign_in(session, throttle_keys(data.username, client))
    user = session.scalar(select(User).where(User.username == data.username))
    return user.password_hash if user is not None and user.active else None


def verify_password(password_hash: str | None, password: SecretStr) -> str | None:
    """Second step, outside any transaction: the hash the password matched, or None. Unknown
    users are checked against a dummy hash so timing does not reveal which names exist."""
    with HASHING:
        try:
            HASHER.verify(password_hash or DUMMY_HASH, password.get_secret_value())
        except (VerificationError, InvalidHashError):
            return None
    return password_hash


def record_login(session: Session, data: LoginRequest, client: str, matched_hash: str | None,
                 old_token: str | None) -> tuple[str, SessionRecord] | None:
    """Final step: count a failure, or start a session if the matched hash is still the user's."""
    keys = throttle_keys(data.username, client)
    lock_sign_in(session, keys)  # Concurrent failures may have closed a bucket meanwhile.
    now = stamp()
    user = session.scalar(select(User).where(User.username == data.username).with_for_update())
    if matched_hash is None or user is None or not user.active or user.password_hash != matched_hash:
        count_failure(session, keys)
        return None  # Caller commits throttle counters before returning generic 401.
    # A successful sign-in clears only this client's failures for the username.
    session.execute(delete(LoginThrottle).where(LoginThrottle.key == keys['client']))
    if HASHER.check_needs_rehash(user.password_hash):
        with HASHING:
            user.password_hash = HASHER.hash(data.password.get_secret_value())
    session.execute(delete(LoginSession).where((LoginSession.expires_at <= now) | (LoginSession.last_seen_at <= now - IDLE)))
    if old_token:
        session.execute(delete(LoginSession).where(LoginSession.token_hash == digest(old_token)))
    token = secrets.token_urlsafe(32)
    expires = now + LIFETIME
    session.add(LoginSession(token_hash=digest(token), user_id=user.id, expires_at=expires, last_seen_at=now))
    return token, SessionRecord(identity=identity(user), csrf_token=csrf(token), expires_at=expires)


def count_failure(session: Session, keys: dict[str, str]) -> None:
    now = stamp()
    for name, _, window in THROTTLES:
        bucket = session.get(LoginThrottle, keys[name])
        if bucket is None:
            bucket = LoginThrottle(key=keys[name], attempts=0, expires_at=now + window)
            session.add(bucket)
        bucket.attempts += 1


def hash_password(password: SecretStr) -> str:
    """Hash before opening a transaction; Argon2 work holds neither a connection nor a lock."""
    with HASHING:
        return HASHER.hash(password.get_secret_value())


def own_credentials(session: Session, who: Identity, client: str) -> tuple[str, dict[str, str]]:
    """First step of a password change: the current hash, behind the same sign-in limits."""
    user = session.get(User, who.user_id) if who.user_id else None
    if user is None or not user.active:
        raise DataError(404, 'NOT_FOUND', 'User not found in this organization.')
    keys = throttle_keys(user.username, client)
    lock_sign_in(session, keys)
    return user.password_hash, keys


def finish_password_change(session: Session, who: Identity, keys: dict[str, str], matched: str | None,
                           new_hash: str, token: str) -> bool:
    """Store the new hash if the current password matched; the user's other sessions end, this one stays."""
    lock_sign_in(session, keys)
    user = session.scalar(select(User).where(User.id == who.user_id).with_for_update())
    if matched is None or user is None or not user.active or user.password_hash != matched:
        count_failure(session, keys)  # A stolen session cannot guess the password freely.
        return False
    user.password_hash = new_hash
    session.execute(delete(LoginSession).where(LoginSession.user_id == user.id, LoginSession.token_hash != digest(token)))
    return True


def reset_password(session: Session, who: Identity, key: UUID, new_hash: str) -> None:
    """An administrator sets another user's password; all of that user's sessions end."""
    require_admin(who); lock_owner(session, who.organization_id); require_current_admin(session, who)
    user = session.scalar(select(User).where(User.id == key, User.organization_id == who.organization_id).with_for_update())
    if user is None:
        raise DataError(404, 'NOT_FOUND', 'User not found in this organization.')
    if user.id == who.user_id:
        raise DataError(409, 'OWN_PASSWORD', 'Change your own password from Personal preferences.')
    user.password_hash = new_hash
    session.execute(delete(LoginSession).where(LoginSession.user_id == user.id))


def login(session: Session, data: LoginRequest, old_token: str | None, client: str = 'unknown') -> tuple[str, SessionRecord] | None:
    """All three steps in the caller's transaction (first-use setup signs in the account it just created)."""
    matched = verify_password(stored_credentials(session, data, client), data.password)
    return record_login(session, data, client, matched, old_token)


def authenticate(session: Session, token: str | None, csrf_token: str | None, writing: bool, *, activity: bool = True) -> SessionRecord:
    if not token or len(token) != 43:
        raise DataError(401, 'SESSION_REQUIRED', 'Sign in to continue.')
    row = session.get(LoginSession, digest(token))
    now = stamp()
    user = session.get(User, row.user_id) if row else None
    if row is None or user is None or not user.active or row.expires_at <= now or row.last_seen_at <= now - IDLE:
        raise DataError(401, 'SESSION_REQUIRED', 'Sign in to continue.')
    expected = csrf(token)
    if writing and (csrf_token is None or not hmac.compare_digest(expected.encode(), csrf_token.encode())):
        raise DataError(403, 'CSRF_REQUIRED', 'Refresh the page before submitting changes.')
    if activity:  # Background polling must not keep an unattended session alive.
        row.last_seen_at = now
    return SessionRecord(identity=identity(user), csrf_token=expected, expires_at=row.expires_at)


def current_session(request: Request, token: str | None = Security(SESSION_COOKIE), local_token: str | None = Security(LOCAL_SESSION_COOKIE)) -> SessionRecord:
    settings = request.app.state.settings
    if settings.app_mode == 'local_demo':
        result = SessionRecord(identity=Identity(organization_id=settings.demo_organization_id, user_id=None,
                               username='local-demo', display_name='Local demo', role='admin'), csrf_token=None, expires_at=None)
    else:
        token = request.cookies.get(cookie_name(settings))
        with Session(request.app.state.engine) as session, session.begin():
            reading = request.method in {'GET', 'HEAD', 'OPTIONS'}
            result = authenticate(session, token, request.headers.get('x-csrf-token'), not reading,
                                  activity=not (reading and request.headers.get('x-vrp-background') == '1'))
    request.state.identity = result.identity
    return result


def who(request: Request) -> Identity:
    return request.state.identity
