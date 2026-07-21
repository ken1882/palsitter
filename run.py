import argparse
import sys

from module.games import get_game
from module.instances import load_instance


def main() -> int:
    parser = argparse.ArgumentParser(description="Run one Palsitter-managed game server")
    parser.add_argument("profile")
    parser.add_argument("--interval", type=int, default=60)
    args = parser.parse_args()
    record = load_instance(args.profile)
    adapter = get_game(record.game)
    if not adapter.runnable:
        print(f"{adapter.display_name} support is not implemented", file=sys.stderr)
        return 2
    adapter.bootstrap(record, print)
    adapter.supervise(record, print, lambda: False, lambda _: None, args.interval)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
