import re
import unittest
from pathlib import Path

from app.cortex.tools.error_envelope import ALLOWED_ERROR_CLASSES


class CortexErrorClassEmitterTests(unittest.TestCase):
    def test_cortex_error_classes_are_in_allowlist(self):
        root = Path(__file__).resolve().parents[1]
        cortex_root = root / "app" / "cortex"
        emitted = set()
        for path in cortex_root.rglob("*.py"):
            source = path.read_text(encoding="utf-8")
            emitted.update(re.findall(r'error_class="([a-z_]+)"', source))
        self.assertTrue(emitted)
        self.assertTrue(emitted.issubset(ALLOWED_ERROR_CLASSES))


if __name__ == "__main__":
    unittest.main()
