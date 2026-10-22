"""Serve the chat page and API: python -m corpusstudy.api [--host H] [--port P]"""

from __future__ import annotations

import argparse

import uvicorn

from corpusstudy.api.app import create_app
from corpusstudy.config import get_settings


def main(argv: list[str] | None = None) -> None:
    settings = get_settings()
    parser = argparse.ArgumentParser(prog="python -m corpusstudy.api")
    parser.add_argument("--host", default=settings.api_host)
    parser.add_argument("--port", type=int, default=settings.api_port)
    args = parser.parse_args(argv)
    uvicorn.run(create_app(), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
