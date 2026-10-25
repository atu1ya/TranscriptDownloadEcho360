"""``python -m echo360dl`` -- GUI by default, CLI with arguments."""

import sys


def main() -> None:
    if len(sys.argv) > 1:
        from .cli import main as cli_main

        raise SystemExit(cli_main(sys.argv[1:]))
    from .gui import main as gui_main

    gui_main()


if __name__ == "__main__":
    main()