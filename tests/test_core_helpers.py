import unittest

from echo360dl.core import parse_cookie_header, safe_filename, split_section_url


class CoreHelperTests(unittest.TestCase):
    def test_section_url_forms(self):
        host, section = split_section_url(
            "https://echo360.net.au/section/11111111-2222-3333-4444-555555555555/home"
        )
        self.assertEqual(host, "https://echo360.net.au")
        self.assertEqual(section, "11111111-2222-3333-4444-555555555555")

    def test_cookie_header(self):
        jar = parse_cookie_header('Cookie: ECHO_JWT=abc.def; other="1"; junk')
        self.assertEqual(jar["ECHO_JWT"], "abc.def")
        self.assertEqual(jar["other"], "1")
        self.assertNotIn("junk", jar)

    def test_safe_filename(self):
        self.assertEqual(safe_filename('L1: a/b?c*  "x"'), "L1- a-b-c- -x")


if __name__ == "__main__":
    unittest.main(verbosity=2)
