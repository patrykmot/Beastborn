"""Start Beastborn: python main.py --players 4 --seed 42"""
from __future__ import annotations

import argparse

from beastborn.game import GameConfig, new_game


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Beastborn - turn-based beast tactics")
    parser.add_argument("--players", type=int, default=2, choices=[2, 3, 4], help="hot-seat players (2-4)")
    parser.add_argument("--seed", type=int, default=None, help="map seed (same seed = same map)")
    parser.add_argument("--width", type=int, default=12, help="map width in tiles")
    parser.add_argument("--height", type=int, default=12, help="map height in tiles")
    return parser.parse_args(argv)


def main(argv=None) -> None:
    args = parse_args(argv)
    gsm = new_game(args.players, seed=args.seed, config=GameConfig(width=args.width, height=args.height))
    print(f"Beastborn - {args.players} players, seed {gsm.view().seed}")

    from beastborn.ui.pygame_ui.app import PygameFrontend  # pygame only needed for this frontend

    PygameFrontend().run(gsm)


if __name__ == "__main__":
    main()
