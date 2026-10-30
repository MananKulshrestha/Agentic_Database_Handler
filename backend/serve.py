"""Start the API with a Windows-compatible event loop for psycopg async."""

import argparse
import asyncio
import sys

import uvicorn


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    config = uvicorn.Config("backend.app.main:app", host=args.host, port=args.port, loop="asyncio")
    server = uvicorn.Server(config)
    try:
        if sys.platform == "win32":
            with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
                runner.run(server.serve())
        else:
            server.run()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
