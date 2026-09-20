"""Small async wrapper around BACpypes3 used by the regression suite."""

from __future__ import annotations

import asyncio
from typing import Any

from bacpypes3.app import Application
from bacpypes3.argparse import SimpleArgumentParser
from bacpypes3.constructeddata import AnyAtomic

from .config import TestConfig


def _unwrap(value: Any) -> Any:
    """Convert BACpypes atomic wrappers to ordinary Python values."""
    return value.get_value() if isinstance(value, AnyAtomic) else value


class BacnetClient:
    def __init__(self, config: TestConfig) -> None:
        self.config = config
        self.app: Application | None = None

    async def open(self) -> None:
        args = SimpleArgumentParser().parse_args(
            [
                "--address", self.config.network.local_address,
                "--instance", str(self.config.network.local_device_instance),
                "--name", "BACnet Regression Test PC",
            ]
        )
        self.app = Application.from_args(args)

    def close(self) -> None:
        if self.app is not None:
            self.app.close()
            self.app = None

    def _app(self) -> Application:
        if self.app is None:
            raise RuntimeError("BACnet client has not been opened")
        return self.app

    async def discover_expected_device(self) -> str:
        """Broadcast Who-Is and return the responding controller address."""
        responses = await asyncio.wait_for(
            self._app().who_is(
                self.config.controller.device_instance,
                self.config.controller.device_instance,
            ),
            timeout=self.config.network.timeout_seconds,
        )
        for response in responses:
            identifier = response.iAmDeviceIdentifier
            if identifier[1] == self.config.controller.device_instance:
                return str(response.pduSource)
        raise AssertionError(
            f"No I-Am received for Device Instance {self.config.controller.device_instance}"
        )

    async def read(self, object_identifier: str, property_identifier: str) -> Any:
        value = await asyncio.wait_for(
            self._app().read_property(
                self.config.controller.address,
                object_identifier,
                property_identifier,
            ),
            timeout=self.config.network.timeout_seconds,
        )
        return _unwrap(value)

    async def write(
        self,
        object_identifier: str,
        property_identifier: str,
        value: Any,
        priority: int,
    ) -> Any:
        # BACpypes3 parses this value according to the target BACnet property type.
        return await asyncio.wait_for(
            self._app().write_property(
                self.config.controller.address,
                object_identifier,
                property_identifier,
                str(value),
                None,
                priority,
            ),
            timeout=self.config.network.timeout_seconds,
        )

