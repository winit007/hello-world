"""Entry point of the standalone Windows program (StockAgent.exe, built with PyInstaller).

Double-clicking it starts the app and opens the browser. Any arguments run the command line instead,
e.g. `StockAgent.exe screen --universe fno` or `StockAgent.exe shortcut`.
"""
import sys

import stock_agent._bundle  # noqa: F401  (pull every module into the program)
from stock_agent.__main__ import main

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:] or ["app"]))
