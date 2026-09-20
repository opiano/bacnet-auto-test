import math

import pytest


@pytest.mark.asyncio
async def test_test_only_output_can_be_written(bacnet_client, test_config):
    setting = test_config.safe_write
    if not setting.enabled:
        pytest.skip("safe_write.enabled is false; output writes are intentionally disabled")

    await bacnet_client.write(
        setting.object_identifier,
        setting.property_identifier,
        setting.value,
        setting.priority,
    )
    actual = await bacnet_client.read(setting.object_identifier, setting.property_identifier)
    expected = setting.expected_readback if setting.expected_readback is not None else setting.value
    if isinstance(expected, (int, float)):
        assert math.isclose(float(actual), float(expected), rel_tol=1e-6, abs_tol=1e-6)
    else:
        assert str(actual) == str(expected)

    if setting.relinquish_after_test:
        await bacnet_client.write(
            setting.object_identifier,
            setting.property_identifier,
            "null",
            setting.priority,
        )

