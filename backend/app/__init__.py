"""Agentic database handler backend."""

import asyncio
import sys

# psycopg's async driver needs the selector loop on Windows.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
