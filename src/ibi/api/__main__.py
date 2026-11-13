"""Run locally: `python -m ibi.api` (binds to 127.0.0.1 only; no auth yet)."""

import uvicorn

from ibi.api.app import create_app
from ibi.config import get_settings

if __name__ == "__main__":
    uvicorn.run(create_app(), host="127.0.0.1", port=get_settings().api_port)
