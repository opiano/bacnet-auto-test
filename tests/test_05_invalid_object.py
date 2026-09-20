import pytest

from bacpypes3.apdu import ErrorRejectAbortNack


@pytest.mark.asyncio
async def test_unknown_object_returns_a_bacnet_error(bacnet_client):
    # BACnet object instance maximum is used as a deliberately absent test object.
    with pytest.raises(ErrorRejectAbortNack):
        await bacnet_client.read("analog-input,4194303", "present-value")

