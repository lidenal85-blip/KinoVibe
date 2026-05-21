"""
conftest.py — Shared fixtures for KinoVibe backend tests
"""
import sys
import os
import asyncio
import pytest

# Make backend importable
sys.path.insert(0, "/var/www/kinovibe/backend")
sys.path.insert(0, "/opt/leviathan_engine")

BASE_URL = "http://localhost:8110"


@pytest.fixture(scope="session")
def base_url():
    return BASE_URL


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()
