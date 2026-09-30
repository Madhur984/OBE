"""
Vercel entrypoint.

Vercel's Python runtime looks for an ASGI application named `app` in this
module and serves it directly -- there is no uvicorn process, so nothing
here starts a server.

The repository keeps the application under `app/`, one level up from this
file, so the parent directory goes on the path before the import. Vercel
sets the project's root directory to `autoobe/backend`, which makes that
parent the bundle root.
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app.api.main import app  # noqa: E402  (path set above)

__all__ = ["app"]
