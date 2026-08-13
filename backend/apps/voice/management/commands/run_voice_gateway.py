from __future__ import annotations

import asyncio
import os
import signal
import socket

from django.core.management.base import BaseCommand

from voice.controller import run_worker


class Command(BaseCommand):
    help = "Run the dedicated async Realtime Voice controller worker."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true")

    def handle(self, *args, **options):
        async def main():
            stop = asyncio.Event()
            loop = asyncio.get_running_loop()
            for signal_name in (signal.SIGTERM, signal.SIGINT):
                try:
                    loop.add_signal_handler(signal_name, stop.set)
                except NotImplementedError:
                    pass
            worker_id = f"{socket.gethostname()}-{os.getpid()}"
            await run_worker(worker_id=worker_id, once=options["once"], stop_event=stop)

        asyncio.run(main())
