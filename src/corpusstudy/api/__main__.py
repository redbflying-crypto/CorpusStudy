"""Serve the chat page and API.

    python -m corpusstudy.api [--prepare] [--host H] [--port P]

--prepare first brings the corpus and the search index up to date (corpusstudy.prepare), as
the Docker container does on every start. Answers are logged one line each (route, outcome,
tokens, cost, time; never the question).
"""

from __future__ import annotations

import argparse
import logging
import sys

import uvicorn

from corpusstudy.api.app import create_app
from corpusstudy.config import get_settings


def main(argv: list[str] | None = None) -> int:
    settings = get_settings()
    parser = argparse.ArgumentParser(prog="python -m corpusstudy.api")
    parser.add_argument("--host", default=settings.api_host)
    parser.add_argument("--port", type=int, default=settings.api_port)
    parser.add_argument("--prepare", action="store_true",
                        help="build the corpus and the index first when needed")  # fmt: skip
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    if args.prepare:
        from corpusstudy.prepare import PrepareError, prepare

        log = logging.getLogger("corpusstudy.prepare")
        try:
            prepare(log=log.info)
        except PrepareError as exc:
            log.error("%s", exc)
            return 1
    if not settings.has_api_key:
        logging.getLogger("corpusstudy.api").warning(
            "ANTHROPIC_API_KEY is not set: verse lookups and safety messages work; other "
            "questions return 503 until it is set (see .env.example)"
        )
    uvicorn.run(create_app(), host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    sys.exit(main())
