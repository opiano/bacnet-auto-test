import pytest


@pytest.mark.asyncio
async def test_device_object_identity(bacnet_client, test_config):
    device = f"device,{test_config.controller.device_instance}"
    for property_name in ("object-name", "vendor-name", "model-name", "firmware-revision"):
        value = await bacnet_client.read(device, property_name)
        assert value not in (None, ""), f"{property_name} must be populated"


@pytest.mark.asyncio
async def test_expected_device_metadata(bacnet_client, test_config):
    device = f"device,{test_config.controller.device_instance}"
    property_map = {
        "vendor_name": "vendor-name",
        "model_name": "model-name",
    }
    for config_name, property_name in property_map.items():
        if config_name in test_config.controller.expected:
            actual = await bacnet_client.read(device, property_name)
            assert str(actual) == str(test_config.controller.expected[config_name])

