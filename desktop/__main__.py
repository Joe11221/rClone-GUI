import argparse
import os
import sys


def main():
    if os.name != "nt":
        sys.exit("The desktop app currently supports Windows only.")

    # Windowed builds have no console, and libraries that print would crash.
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w")

    parser = argparse.ArgumentParser(prog="rclone-gui")
    parser.add_argument(
        "--serve", action="store_true", help="run only the server, without a tray icon"
    )
    parser.add_argument(
        "--background", action="store_true", help="start in the tray, without the window"
    )
    args = parser.parse_args()

    # Imported here because desktop.server monkey-patches on import, which must
    # never happen in the tray process.
    if args.serve:
        from desktop import server

        sys.exit(server.run())

    from desktop import tray

    sys.exit(tray.run(show_window=not args.background))


if __name__ == "__main__":
    main()
