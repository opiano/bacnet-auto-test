import math

import pytest


@pytest.mark.asyncio
async def test_required_object_properties_are_readable(bacnet_client, test_config):
    assert test_config.required_objects, "Configure at least one required BACnet object"
    for item in test_config.required_objects:
        actual = await bacnet_client.read(item.object_identifier, item.property_identifier)
        assert actual is not None, f"{item.name}: returned None"
        if item.has_expected and isinstance(item.expected, float):
            assert math.isclose(float(actual), item.expected, rel_tol=1e-6, abs_tol=1e-6), item.name
        elif item.has_expected:
            assert str(actual) == str(item.expected), item.name

