# -*- coding: utf-8 -*-
"""ASCII JSON without IronPython's erroneous UTF-8 byte-string detection."""
import json

try:
    text_type = unicode
    string_types = (basestring,)
except NameError:
    text_type = str
    string_types = (str,)


def unicode_tree(value):
    if isinstance(value, string_types):
        return text_type(value)
    if isinstance(value, dict):
        return {unicode_tree(key): unicode_tree(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [unicode_tree(item) for item in value]
    return value


def ascii_envelope(value):
    # pyRevit's bundled encoder mistakes strings containing Latin-1 symbols
    # (e.g. cubic metres with Cyrillic text) for UTF-8 bytes in ensure_ascii mode.
    # Let its normal encoder handle JSON syntax, then escape Unicode codepoints.
    encoded = json.dumps(unicode_tree(value), ensure_ascii=False, allow_nan=False)
    parts = []
    for character in encoded:
        point = ord(character)
        if point < 128:
            parts.append(character)
        elif point <= 0xffff:
            parts.append("\\u%04x" % point)
        else:
            point -= 0x10000
            parts.append("\\u%04x\\u%04x" % (0xd800 + (point >> 10), 0xdc00 + (point & 1023)))
    return {"payload_json": "".join(parts)}
