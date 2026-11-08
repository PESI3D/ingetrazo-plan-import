"""Imperial plan sets: scales, feet-and-inch levels, English storeys."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from plan_import_tool import pdf_vectors as pv  # noqa: E402


class Imperial(unittest.TestCase):
    def test_scales(self):
        for txt, n in (('1/4" = 1\'-0"', 48), ('SCALE: 1/8" = 1\'-0"', 96),
                       ('1 1/2" = 1\'-0"', 8), ('3/16"=1\'-0"', 64),
                       ('1" = 20\'', 240), ("M 1:100", 100)):
            self.assertEqual(pv._scale_in(txt), n, txt)

    def test_levels(self):
        for txt, v in (('+10\'-6"', 10.5), ('EL. 12\'-0 1/2"', 12 + 0.5 / 12),
                       ('±0\'-0"', 0.0), ('-8\'-4"', -8 - 4 / 12),
                       ("+3,00", 3.0), ("-1,25", -1.25)):
            self.assertAlmostEqual(pv.level_value(txt), v, 6, txt)
        self.assertIsNone(pv.level_value("Erdgeschoss"))

    def test_storeys(self):
        self.assertEqual(pv.story_key("SECOND FLOOR PLAN"), pv.story_key("Level 2"))
        self.assertEqual(pv.story_key("2nd Floor"), "fl2")
        self.assertEqual(pv.story_key("1. Obergeschoss"), "1og")
        self.assertTrue(pv._is_storey_name("First Floor Plan"))
        self.assertFalse(pv._is_storey_name("Building Section A"))

    def test_title(self):
        self.assertEqual(pv._title_from('FIRST FLOOR PLAN  SCALE: 1/4" = 1\'-0"'),
                         ("FIRST FLOOR PLAN", 48))
        self.assertEqual(pv._title_from("Erdgeschoss 1:100"), ("Erdgeschoss", 100))


if __name__ == "__main__":
    unittest.main()
