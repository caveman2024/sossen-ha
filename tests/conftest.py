"""Make custom_components.sossen importable without running its __init__.

The package __init__ imports Home Assistant and tinytuya; the protocol codec
and the register map need neither. Registering the two packages as bare
modules lets each test import exactly the submodule it needs: protocol tests
run with no Home Assistant at all, coordinator tests load ha_stubs first.
"""

import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

for name, path in (
    ("custom_components", ROOT / "custom_components"),
    ("custom_components.sossen", ROOT / "custom_components" / "sossen"),
):
    if name not in sys.modules:
        pkg = types.ModuleType(name)
        pkg.__path__ = [str(path)]
        sys.modules[name] = pkg
