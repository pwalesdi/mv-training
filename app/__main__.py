"""Start the calendar prototype: .venv/bin/python -m app [--port 5050] [--db path/to/training.sqlite]

Always binds to 127.0.0.1 - the data is student data and must not be reachable from the network.
"""
import argparse

from .server import DB, create_app


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", type=int, default=5050)
    ap.add_argument("--db", default=str(DB), help="database to read (default data/build/training.sqlite)")
    args = ap.parse_args()
    print(f"MV Training prototype: http://127.0.0.1:{args.port}  (Ctrl+C to stop)")
    create_app(args.db).run(host="127.0.0.1", port=args.port, debug=False)


if __name__ == "__main__":
    main()
