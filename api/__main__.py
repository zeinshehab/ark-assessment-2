"""Start the service:  python -m api  [--host 0.0.0.0] [--port 8000] [--reload]

Configuration is loaded and checked BEFORE the server starts. A bad config exits with code 2 and one clear log
line naming the problem (never the secret values). In production that keeps the container unhealthy, so a bad
deployment is caught by the readiness check and rolled back.
"""
import argparse
import logging
import sys

import uvicorn

from reviewer_loop import config
from reviewer_loop.logs import setup_logging


def main() -> None:
    parser = argparse.ArgumentParser(description="Code review orchestrator API + UI")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--reload", action="store_true", help="development only: restart on code changes")
    args = parser.parse_args()

    try:
        settings = config.get_settings()
    except config.ConfigError as exc:
        logging.basicConfig(format='{"level": "CRITICAL", "event": "startup failed", "error": "%(message)s"}')
        logging.getLogger("api").critical(str(exc).replace('"', "'"))
        sys.exit(2)

    setup_logging(settings)
    # log_config=None: uvicorn's loggers go through our handler (JSON + redaction); we log requests ourselves
    uvicorn.run("api.main:app", host=args.host, port=args.port, reload=args.reload and not settings.production,
                log_config=None, access_log=False, proxy_headers=False)


if __name__ == "__main__":
    main()
