"""Allow `python -m terminal_ws` from the api venv."""

import asyncio

from terminal_ws.server import main

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
