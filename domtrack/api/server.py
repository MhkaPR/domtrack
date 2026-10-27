"""uvicorn launcher for the domtrack REST API.

Importing uvicorn lazily keeps ``domtrack.api`` importable (for tests that
only touch the ASGI app) without the server extra installed.
"""

from __future__ import annotations

import os
from typing import Optional


def serve(
    host: str = "127.0.0.1",
    port: int = 8000,
    reload: bool = False,
    workers: int = 1,
    trace_dir: Optional[str] = None,
    log_level: str = "info",
) -> int:
    """Run the API with uvicorn. Returns the process exit code."""
    try:
        import uvicorn
    except ImportError:
        print(
            "ERROR: the API server needs extra dependencies.\n"
            "       Install them with:  pip install 'domtrack[api]'",
            file=__import__("sys").stderr,
        )
        return 1

    if trace_dir:
        os.environ["DOMTRACK_API_TRACE_DIR"] = trace_dir

    # `workers` and `reload` are mutually exclusive in uvicorn; reload wins.
    if reload:
        uvicorn.run(
            "domtrack.api.app:app",
            host=host,
            port=port,
            reload=True,
            log_level=log_level,
        )
    else:
        uvicorn.run(
            "domtrack.api.app:app",
            host=host,
            port=port,
            workers=workers,
            log_level=log_level,
        )
    return 0
