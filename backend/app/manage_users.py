"""Operator-only account provisioning/recovery; passwords are read from the terminal."""
import argparse
from getpass import getpass
import sys

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.accounts import LoginSession, User
from app.auth import CreateUser, HASHER, create_user
from app.config import Settings
from app.db import make_engine
from app.records import DataError, lock_owner


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['create-admin', 'reset-password'])
    parser.add_argument('username')
    parser.add_argument('--name', default='Administrator')
    args = parser.parse_args()
    if not sys.stdin.isatty():
        raise SystemExit('Use an interactive terminal; passwords are never accepted as command arguments.')
    password = getpass('New password (15–128 characters): ')
    if password != getpass('Repeat new password: '):
        raise SystemExit('Passwords did not match.')
    data = CreateUser(username=args.username, display_name=args.name, password=password, role='admin')
    settings = Settings(); engine = make_engine(settings)
    try:
        with Session(engine) as session, session.begin():
            if args.action == 'create-admin':
                create_user(session, settings.demo_organization_id, data)
            else:
                lock_owner(session, settings.demo_organization_id)
                user = session.scalar(select(User).where(User.organization_id == settings.demo_organization_id,
                                                        User.username == data.username).with_for_update())
                if user is None:
                    raise DataError(404, 'NOT_FOUND', 'Account not found in this installation.')
                user.password_hash = HASHER.hash(password)
                session.execute(delete(LoginSession).where(LoginSession.user_id == user.id))
    except DataError as error:
        raise SystemExit(error.message) from None
    finally:
        engine.dispose()
    print('Account updated. Any reset account sessions were revoked.')


if __name__ == '__main__':
    main()
