import os
import tempfile

import pytest


@pytest.fixture(autouse=True)
def private_dir(monkeypatch):
    with tempfile.TemporaryDirectory() as d:
        monkeypatch.setenv("CW_DATA_DIR", d)
        from content_workbench import db
        db.reset_for_tests()
        yield d
        db.reset_for_tests()
