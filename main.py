"""Start the Beastborn web server locally: python main.py  ->  open http://127.0.0.1:8000

On PythonAnywhere the game is served through gercio_eu_pythonanywhere_com_wsgi.py instead.
"""
from __future__ import annotations

import argparse
import logging
from collections.abc import Sequence

from beastborn.constant import DEFAULT_HOST, DEFAULT_PORT, GAME_TITLE, LOG_FORMAT


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser: argparse.ArgumentParser = argparse.ArgumentParser(description=f"{GAME_TITLE} - turn-based beast tactics (web server)")
    parser.add_argument("--host", default=DEFAULT_HOST, help="interface to listen on (0.0.0.0 = whole network)")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="HTTP port")
    parser.add_argument("--reload", action="store_true", help="restart on code changes (development)")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> None:
    from beastborn.ui.web.app import create_app  # lazy: --help works without Flask installed

    logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)  # INFO logs (e.g. bot searches) on the console

    args: argparse.Namespace = parse_args(argv)
    print(f"{GAME_TITLE} running on http://{args.host}:{args.port}  (Ctrl+C to stop)")
    # One process on purpose: games are kept in this process's memory. Threads are fine (per-game locks).
    create_app().run(host=args.host, port=args.port, threaded=True, use_reloader=args.reload, debug=False)


if __name__ == "__main__":
    main()
