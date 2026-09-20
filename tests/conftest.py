from __future__ import annotations

import os
from pathlib import Path

import pytest

from bacnet_test.client import BacnetClient
from bacnet_test.config import TestConfig, load_config


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--bacnet-config",
        default=os.getenv("BACNET_TEST_CONFIG", "config/controller.yaml"),
        help="Path to the controller YAML configuration file.",
    )


@pytest.fixture(scope="session")
def test_config(pytestconfig: pytest.Config) -> TestConfig:
    path = Path(pytestconfig.getoption("bacnet_config"))
    if not path.exists():
        pytest.fail(
            f"BACnet configuration not found: {path}. "
            "Copy config/controller.example.yaml to config/controller.yaml and edit it."
        )
    return load_config(path)


@pytest.fixture(scope="session")
async def bacnet_client(test_config: TestConfig):
    client = BacnetClient(test_config)
    await client.open()
    try:
        yield client
    finally:
        client.close()

