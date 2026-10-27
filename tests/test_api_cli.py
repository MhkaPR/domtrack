"""Wiring tests for the `serve` command and the API module entry points."""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from domtrack import cli  # noqa: E402
from domtrack.api import __main__ as api_main  # noqa: E402
from domtrack.api import server as api_server  # noqa: E402


def test_unified_cli_lists_serve():
    assert "serve" in cli.USAGE


def test_serve_help_exits_zero(capsys):
    with pytest.raises(SystemExit) as exc:
        api_main.main(["--help"])
    assert exc.value.code == 0
    assert "REST API" in capsys.readouterr().out


def test_serve_parser_defaults():
    args = api_main.build_parser().parse_args([])
    assert args.host == "127.0.0.1"
    assert args.port == 8000
    assert args.workers == 1
    assert args.trace_dir is None


def test_serve_sets_trace_dir_env(monkeypatch, tmp_path):
    recorded = {}

    def fake_run(app, **kwargs):
        recorded["app"] = app
        recorded.update(kwargs)

    import uvicorn

    monkeypatch.setattr(uvicorn, "run", fake_run)
    monkeypatch.delenv("DOMTRACK_API_TRACE_DIR", raising=False)

    rc = api_server.serve(host="0.0.0.0", port=9001, trace_dir=str(tmp_path))
    assert rc == 0
    assert recorded["app"] == "domtrack.api.app:app"
    assert recorded["host"] == "0.0.0.0"
    assert recorded["port"] == 9001
    import os

    assert os.environ["DOMTRACK_API_TRACE_DIR"] == str(tmp_path)
