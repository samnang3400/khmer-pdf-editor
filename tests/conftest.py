import os
import tempfile


def pytest_configure(config):
    # built font decoders are cached on disk: keep the tests' copies out of the user's cache
    os.environ.setdefault("KHMERPDF_CACHE", tempfile.mkdtemp(prefix="khmerpdf-test-cache-"))
