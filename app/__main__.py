"""Start the calendar prototype: .venv/bin/python -m app [--port 5050]

Always binds to 127.0.0.1 - the data is student data and must not be reachable from the network.
"""
import argparse

from .server import create_app


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", type=int, default=5050)
    args = ap.parse_args()
    print(f"MV Training prototype: http://127.0.0.1:{args.port}  (Ctrl+C to stop)")
    create_app().run(host="127.0.0.1", port=args.port, debug=False)


if __name__ == "__main__":
    main()
