import unittest

from lain_admin_cli.registry import PREPARE


class TestMethods(unittest.TestCase):
    def test_add(self):
        self.assertEqual(PREPARE, "prepare")


if __name__ == '__main__':
    unittest.main()
