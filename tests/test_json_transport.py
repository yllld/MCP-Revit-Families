# -*- coding: utf-8 -*-
import json
import unittest

from core.json_transport import ascii_envelope


class JsonTransportTests(unittest.TestCase):
    def test_cyrillic_units_chinese_and_non_bmp_roundtrip(self):
        value = {"ADSK_Примечание": "4,5 м³/мин; +2…+5 °C; 0,01 µm; 储气罐; 😀",
                 "escaped": 'quote" backslash\\ newline\n literal \\u1234',
                 "nested": [None, True, 30.0, {"кириллица": "значение"}]}
        envelope = ascii_envelope(value)
        envelope["payload_json"].encode("ascii")
        self.assertEqual(value, json.loads(json.loads(json.dumps(envelope))["payload_json"]))

    def test_nonfinite_numbers_rejected(self):
        with self.assertRaises(ValueError):
            ascii_envelope({"dimension": float("nan")})
