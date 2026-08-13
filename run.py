"""
Entry point for the Caravan Fresh platform.

Usage:
    python run.py init      # create tables + seed demo data
    python run.py reset     # wipe and re-seed
    python run.py           # run the development server (auto-seeds first run)
"""
import sys

from caravan import create_app
from caravan import data

app = create_app()


def _init(force=False):
    with app.app_context():
        data.init_schema()
        from caravan.seed import seed
        print("Database:", seed(force=force))


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    if cmd == "init":
        _init(force=False)
    elif cmd == "reset":
        _init(force=True)
    else:
        with app.app_context():
            data.init_schema()
            from caravan.seed import seed
            print("Database:", seed(force=False))
        app.run(host="0.0.0.0", port=5000, debug=True)
