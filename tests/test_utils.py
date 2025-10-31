from hgpaktool.utils import padding, roundup


def test_roundup():
    assert roundup(0) == 0
    assert roundup(1) == 0x10
    assert roundup(0xF) == 0x10
    assert roundup(0x10) == 0x10
    assert roundup(0x11) == 0x20


def test_padding():
    assert padding(0) == 0
    assert padding(1) == 0xF
    assert padding(0xF) == 1
    assert padding(0x10) == 0
    assert padding(0x11) == 0xF
