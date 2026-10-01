"""Local admin commands (run from repo root with PYTHONPATH=backend):

    python -m app.auth hash-password          # prompts twice, input is not echoed
    python -m app.auth generate-secret        # value for SESSION_SECRET
    python -m app.auth revoke-sessions --user dispatcher | --all
"""
import argparse
import getpass
import secrets
import sys

from app.auth.passwords import MIN_PASSWORD_CHARS, hash_password
from app.auth.service import USER_ROLES


def _hash(args) -> int:
    if args.stdin:   # for automation: one line on stdin, still never echoed by this tool
        password = sys.stdin.readline().rstrip("\r\n")
    else:
        password = getpass.getpass("Новый пароль: ")
        if getpass.getpass("Повторите пароль: ") != password:
            print("Пароли не совпадают.", file=sys.stderr)
            return 1
    if len(password) < MIN_PASSWORD_CHARS:
        print(f"Пароль должен содержать не менее {MIN_PASSWORD_CHARS} символов.", file=sys.stderr)
        return 1
    print(hash_password(password))
    return 0


def _secret(args) -> int:
    print(secrets.token_urlsafe(48))
    return 0


def _revoke(args) -> int:
    from psycopg_pool import ConnectionPool

    from app.auth.store import PostgresSessionStore
    from app.settings import Settings

    with ConnectionPool(Settings().database_url.get_secret_value(), min_size=1, max_size=1) as pool:
        count = PostgresSessionStore(pool).revoke_user(None if args.all else args.user)
    print(f"Отозвано сессий: {count}. Открытые WebSocket закроются в течение WS_SESSION_RECHECK_S.")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.auth")
    commands = parser.add_subparsers(dest="command", required=True)
    hashing = commands.add_parser("hash-password", help="print a scrypt hash for *_PASSWORD_HASH")
    hashing.add_argument("--stdin", action="store_true", help="read the password from stdin")
    hashing.set_defaults(run=_hash)
    commands.add_parser("generate-secret", help="print a random SESSION_SECRET").set_defaults(run=_secret)
    revoke = commands.add_parser("revoke-sessions", help="revoke sessions in PostgreSQL")
    target = revoke.add_mutually_exclusive_group(required=True)
    target.add_argument("--user", choices=sorted(USER_ROLES))
    target.add_argument("--all", action="store_true")
    revoke.set_defaults(run=_revoke)
    args = parser.parse_args(argv)
    return args.run(args)


if __name__ == "__main__":
    raise SystemExit(main())
