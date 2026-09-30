"""
Create or reset the department administrator.

    python tools/create_admin.py hod "Head of Department"
    python tools/create_admin.py hod --password "..."      # non-interactive
    python tools/create_admin.py hod --reset               # forgotten password

Deliberately a command and not a web form. An open "create the first admin"
page is a window that anyone who reaches the server before the department does
can climb through, and the department asked for a sign-in page and nothing
else: the administrator hands out every account, including their own.

Whoever can run this already has the machine and the connection string, so it
grants nothing they did not have. It is the recovery path when a password is
lost, which a web form could not safely be.
"""
from __future__ import annotations

import argparse
import getpass
import secrets
import string
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.store import neon


def suggest() -> str:
    """A password worth using, offered when none is given."""
    alphabet = string.ascii_letters + string.digits
    return "-".join("".join(secrets.choice(alphabet) for _ in range(5))
                    for _ in range(3))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("username")
    ap.add_argument("full_name", nargs="?", default="")
    ap.add_argument("--password", default="",
                    help="skip the prompt; use with care, it lands in shell history")
    ap.add_argument("--reset", action="store_true",
                    help="set a new password on an account that already exists")
    ap.add_argument("--teacher", action="store_true",
                    help="create a teacher rather than an administrator")
    args = ap.parse_args()

    if not neon.is_configured():
        print("No DATABASE_URL. Run `neon link` in the project directory first.",
              file=sys.stderr)
        return 2
    neon.ensure_schema()

    password = args.password
    if not password:
        password = getpass.getpass(f"Password for {args.username} (blank to generate): ")
        if not password:
            password = suggest()
            print(f"\n  generated password: {password}\n"
                  f"  Give it to them once, and have them change it after signing in.\n")

    role = neon.TEACHER if args.teacher else neon.ADMIN
    existing = neon._one("SELECT id, role FROM users WHERE username = %s",
                         (args.username.strip().lower(),))
    try:
        if existing:
            if not args.reset:
                print(f"{args.username!r} already exists. Use --reset to set a new "
                      f"password.", file=sys.stderr)
                return 1
            neon.set_password(existing["id"], password)
            neon.log("cli", "reset_password", {"username": args.username})
            print(f"Password reset for {args.username} ({existing['role']}). "
                  f"Every session on that account is signed out.")
        else:
            neon.create_user(username=args.username, password=password, role=role,
                             full_name=args.full_name)
            neon.log("cli", "create_user",
                     {"username": args.username, "role": role, "via": "cli"})
            print(f"Created {args.username} as {role}.")
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print("Sign in at /login .")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
