"""Entry point: `python -m soulbridge` / `soulbridge`."""
from __future__ import annotations

import os

import uvicorn


def main() -> None:
    uvicorn.run(
        "soulbridge.web.server:app",
        host=os.environ.get("SOULBRIDGE_HOST", "0.0.0.0"),
        port=int(os.environ.get("SOULBRIDGE_PORT", "8793")),
        forwarded_allow_ips="*",
        log_level=os.environ.get("SOULBRIDGE_LOG_LEVEL", "info"),
    )


if __name__ == "__main__":
    main()
