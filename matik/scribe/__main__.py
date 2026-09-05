"""Entry point for the Scribe service."""

import asyncio
import sys

from .main import main

sys.exit(asyncio.run(main()))
