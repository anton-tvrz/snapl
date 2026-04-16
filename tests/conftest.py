"""Shared test fixtures for snapl."""

import pytest


@pytest.fixture
def sample_device_config():
    """Sample device configuration for testing."""
    return {
        "hostname": "spine01",
        "platform": "nokia_srlinux",
        "management_ip": "172.20.20.11",
        "role": "spine",
    }
