import argparse
import sys

from module.games import get_game
from module.instances import load_instance


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Palsitter backups")
    parser.add_argument("profile")
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    record = load_instance(args.profile)
    adapter = get_game(record.game)
    service = adapter.backup_service(record)
    if service is None:
        print(f"{adapter.display_name} backup support is not implemented", file=sys.stderr)
        return 2
    if args.once:
        service.create_backup()
    else:
        service.scheduled_loop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
