from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from apps.crm.app import create_app
from myelin.config import Settings


@pytest.fixture
def client(tmp_path):
    settings = replace(Settings.load(), demo_token="test-admin-only", demo_email="demo@test.local",
                       demo_password="synthetic-password")
    with TestClient(create_app(tmp_path / "crm.sqlite", settings)) as c:
        yield c
