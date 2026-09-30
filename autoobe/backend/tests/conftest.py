"""
Test-wide setup.

The workspace API opens its database at import time, so the environment has
to be set before anything imports `app.api.main`. An in-memory database also
means the suite never touches -- or inherits state from -- a developer's real
`autoobe.sqlite3`, which is what makes the API tests order-independent.

**And the suite never sees the department's Neon database.** `neon.database_url()`
finds `.env.local` by walking up the tree, so on a machine where `neon link`
has been run the whole offline suite would otherwise be pointed at production:
the workspace routes would demand a session they have no account for, and a
careless `DELETE` in a fixture would take the real sheet with it.

Setting `DATABASE_URL` to the empty string says "this run has no database",
which `neon.database_url()` honours over the file. The integration tests in
`test_accounts.py` opt back in explicitly, and only ever to the throwaway
branch named by `AUTOOBE_TEST_DATABASE_URL`.
"""
from __future__ import annotations

import os

os.environ.setdefault("AUTOOBE_DB", ":memory:")
os.environ.setdefault("DATABASE_URL", "")
