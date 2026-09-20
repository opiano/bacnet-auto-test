import pytest


@pytest.mark.asyncio
async def test_controller_responds_to_who_is(bacnet_client, test_config):
    address = await bacnet_client.discover_expected_device()
    assert address, f"Device {test_config.controller.device_instance} returned an empty source address"

