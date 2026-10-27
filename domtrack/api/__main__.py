"""``python -m domtrack.api`` — run the REST API server with uvicorn."""

from __future__ import annotations

import argparse
import sys

from .server import serve


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="domtrack serve",
        description="Run the domtrack REST API server.",
    )
    p.add_argument("--host", default="127.0.0.1", help="Bind host (default: 127.0.0.1)")
    p.add_argument("--port", type=int, default=8000, help="Bind port (default: 8000)")
    p.add_argument("--reload", action="store_true", help="Auto-reload on code changes")
    p.add_argument(
        "--workers", type=int, default=1, help="Worker processes (default: 1)"
    )
    p.add_argument("--trace-dir", default=None, help="Directory for traces")
    p.add_argument("--log-level", default="info", help="uvicorn log level")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return serve(
        host=args.host,
        port=args.port,
        reload=args.reload,
        workers=args.workers,
        trace_dir=args.trace_dir,
        log_level=args.log_level,
    )


if __name__ == "__main__":
    sys.exit(main())
