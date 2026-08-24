"""Entry point: `python -m soulscribe` / `soulscribe`."""
from __future__ import annotations

import uvicorn

from .env import env


def main() -> None:
    uvicorn.run(
        "soulscribe.web.server:app",
        host=env("HOST", "0.0.0.0"),
        port=int(env("PORT", "8793")),
        forwarded_allow_ips="*",
        log_level=env("LOG_LEVEL", "info"),
    )


if __name__ == "__main__":
    main()
