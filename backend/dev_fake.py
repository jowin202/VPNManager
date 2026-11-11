"""Run the backend against a simulated OPNsense (development without a firewall).

    cd backend && ../.venv/bin/python dev_fake.py   # Token: dev-token-0123456789
"""

import os
from pathlib import Path

import uvicorn

from app.config import Settings
from app.main import create_app
from tests.fake_opnsense import FakeOpnsense

settings = Settings(
    opnsense_url="https://fake-opnsense.test",
    api_key="fake",
    api_secret="fake",
    verify_tls=True,
    api_token="dev-token-0123456789",
    data_dir=Path("data-dev"),
    verify_timeout=3,
)
app = create_app(settings, transport=FakeOpnsense().transport())

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("PORT", "8000")))
