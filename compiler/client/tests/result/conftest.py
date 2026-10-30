"""The result tests share builders by module name (`from test_durable import inputs`). The root `pytest.ini`
imports tests in `importlib` mode, which puts no test directory on `sys.path`, so this one is added here."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
