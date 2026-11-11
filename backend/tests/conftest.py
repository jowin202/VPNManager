from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.context import AppContext
from app.main import create_app
from app.opnsense.client import OpnsenseClient
from app.opnsense.resources import Opnsense
from app.vpn.metadata import MetadataStore

from .fake_opnsense import FakeOpnsense

TOKEN = "test-token-0123456789"


def make_settings(tmp_path: Path, **kw) -> Settings:
    base = dict(
        opnsense_url="https://fw.test",
        api_key="key",
        api_secret="secret",
        verify_tls=True,
        api_token=TOKEN,
        data_dir=tmp_path,
        verify_timeout=0,
    )
    base.update(kw)
    return Settings(**base)


@pytest.fixture
def fake() -> FakeOpnsense:
    return FakeOpnsense()


@pytest.fixture
def settings(tmp_path) -> Settings:
    return make_settings(tmp_path)


@pytest.fixture
async def ctx(fake, settings):
    client = OpnsenseClient(settings.opnsense_url, "key", "secret", transport=fake.transport())
    c = AppContext(settings=settings, ops=Opnsense.create(client), metadata=MetadataStore(settings.data_dir))
    yield c
    await client.aclose()


@pytest.fixture
def api(fake, settings):
    app = create_app(settings, transport=fake.transport())
    with TestClient(app) as client:
        client.headers["Authorization"] = f"Bearer {TOKEN}"
        yield client
