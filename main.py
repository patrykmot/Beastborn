"""Start the Beastborn web server: python main.py  ->  open http://127.0.0.1:8000"""
from __future__ import annotations

import argparse


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Beastborn - turn-based beast tactics (web server)")
    parser.add_argument("--host", default="127.0.0.1", help="interface to listen on (0.0.0.0 = whole network)")
    parser.add_argument("--port", type=int, default=8000, help="HTTP port")
    parser.add_argument("--reload", action="store_true", help="restart on code changes (development)")
    return parser.parse_args(argv)


def main(argv=None) -> None:
    import uvicorn

    args = parse_args(argv)
    print(f"Beastborn running on http://{args.host}:{args.port}  (Ctrl+C to stop)")
    # One worker on purpose: games are kept in this process's memory.
    uvicorn.run("beastborn.ui.web.app:app", host=args.host, port=args.port, reload=args.reload, workers=1)


if __name__ == "__main__":
    main()
