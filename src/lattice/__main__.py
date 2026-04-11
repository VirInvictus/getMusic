import sys
from lattice.cli import main
from lattice.utils import _reset_terminal

if __name__ == "__main__":
    try:
        sys.exit(main())
    finally:
        _reset_terminal()
