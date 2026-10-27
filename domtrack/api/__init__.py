"""``domtrack.api`` — expose the domtrack CLI as a REST API.

The API is a thin HTTP skin over the same building blocks the command line
uses (:mod:`domtrack.recorder`, :mod:`domtrack.replay`,
:mod:`domtrack.diff`, :mod:`domtrack.tables`, :mod:`domtrack.trace`). Every
endpoint mirrors a CLI command so anything you can do from a shell you can do
over HTTP.

The package depends on FastAPI / uvicorn, which are optional extras::

    pip install 'domtrack[api]'

Start the server with either::

    domtrack serve                 # unified CLI
    python -m domtrack.api         # module entry point

``app`` is a ready-to-serve ASGI application for ``uvicorn domtrack.api:app``.
"""

from __future__ import annotations

from .app import app, create_app

__all__ = ["app", "create_app"]

__version__ = "1.0.0"
