"""Session and installation account endpoints; credentials never enter responses."""
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.accounts import LoginSession, User
from app.auth import (CreateUser, LoginRequest, PasswordChange, PasswordReset, SessionRecord, UpdateUser, UserRecord, Identity,
                      Preferences, update_preferences, cookie_name, set_session_cookie, create_user, current_session, digest,
                      finish_password_change, hash_password, login_client, own_credentials, public_user, record_login,
                      require_admin, require_current_admin, reset_password, stored_credentials, update_user, verify_password, who)
from app.data_api import SourceProblem
from app.records import DataError, lock_owner


def auth_router() -> APIRouter:
    api = APIRouter(prefix='/api/v1', responses={n: {'model': SourceProblem} for n in (401,403,404,409,429)})

    def named_mode(request: Request):
        if request.app.state.settings.app_mode == 'local_demo':
            raise DataError(409, 'DEMO_MODE', 'Named accounts are unavailable in legacy unauthenticated demo mode.')

    @api.post('/session', response_model=SessionRecord, operation_id='login')
    def sign_in(data: LoginRequest, request: Request, response: Response):
        named_mode(request)
        client = login_client(request)
        # Hash outside any transaction, so slow Argon2 work holds neither a pooled connection nor the sign-in lock.
        with Session(request.app.state.engine) as session, session.begin():
            stored = stored_credentials(session, data, client)
        matched = verify_password(stored, data.password)
        with Session(request.app.state.engine) as session, session.begin():
            result = record_login(session, data, client, matched, request.cookies.get(cookie_name(request.app.state.settings)))
        if result is None:
            raise DataError(401, 'LOGIN_FAILED', 'Username or password is incorrect.')
        token, record = result
        set_session_cookie(response, request.app.state.settings, token)
        return record

    @api.get('/session', response_model=SessionRecord, operation_id='getSession')
    def read_session(record: SessionRecord = Depends(current_session)):
        return record

    @api.put('/preferences', response_model=Identity, operation_id='updatePreferences', dependencies=[Depends(current_session)])
    def preferences(data: Preferences, request: Request):
        named_mode(request)
        with Session(request.app.state.engine) as session, session.begin():
            return update_preferences(session, who(request), data)

    @api.put('/session/password', response_model=dict[str, bool], operation_id='changePassword',
             dependencies=[Depends(current_session)], responses={422: {'model': SourceProblem}})
    def change_password(data: PasswordChange, request: Request):
        """Change the signed-in user's password; other sessions of this user end."""
        named_mode(request)
        if data.new_password.get_secret_value() == data.current_password.get_secret_value():
            raise DataError(422, 'PASSWORD_UNCHANGED', 'Choose a new password different from the current one.')
        client, token = login_client(request), request.cookies.get(cookie_name(request.app.state.settings), '')
        with Session(request.app.state.engine) as session, session.begin():
            stored, keys = own_credentials(session, who(request), client)
        matched = verify_password(stored, data.current_password)
        new_hash = hash_password(data.new_password) if matched else ''
        with Session(request.app.state.engine) as session, session.begin():
            changed = finish_password_change(session, who(request), keys, matched, new_hash, token)
        if not changed:
            raise DataError(403, 'PASSWORD_INCORRECT', 'Your current password is incorrect.')
        return {'password_changed': True}

    @api.delete('/session', response_model=dict[str, bool], operation_id='logout', dependencies=[Depends(current_session)])
    def logout(request: Request, response: Response):
        named_mode(request)
        with Session(request.app.state.engine) as session, session.begin():
            session.execute(delete(LoginSession).where(LoginSession.token_hash == digest(request.cookies.get(cookie_name(request.app.state.settings), ''))))
        response.delete_cookie(cookie_name(request.app.state.settings), path='/', secure=request.app.state.settings.app_mode != 'local', httponly=True, samesite='strict')
        return {'signed_out': True}

    def administrator(request: Request):
        named_mode(request); require_admin(who(request))

    admin_dependencies = [Depends(current_session), Depends(administrator)]

    @api.get('/users', response_model=list[UserRecord], operation_id='listUsers', dependencies=admin_dependencies)
    def users(request: Request):
        with Session(request.app.state.engine) as session:
            return [public_user(u) for u in session.scalars(select(User).where(User.organization_id == who(request).organization_id).order_by(User.username))]

    @api.post('/users', response_model=UserRecord, status_code=201, operation_id='createUser', dependencies=admin_dependencies)
    def add_user(data: CreateUser, request: Request):
        with Session(request.app.state.engine) as session, session.begin():
            lock_owner(session, who(request).organization_id)
            require_current_admin(session, who(request))
            return create_user(session, who(request).organization_id, data)

    @api.post('/users/{key}/password', response_model=dict[str, bool], operation_id='resetUserPassword', dependencies=admin_dependencies)
    def reset_user_password(key: UUID, data: PasswordReset, request: Request):
        """Set a new password for another user of this company; their sessions end."""
        new_hash = hash_password(data.new_password)
        with Session(request.app.state.engine) as session, session.begin():
            reset_password(session, who(request), key, new_hash)
        return {'password_reset': True}

    @api.put('/users/{key}', response_model=UserRecord, operation_id='updateUser', dependencies=admin_dependencies)
    def change_user(key: UUID, data: UpdateUser, request: Request):
        with Session(request.app.state.engine) as session, session.begin():
            return update_user(session, who(request), key, data)

    return api
