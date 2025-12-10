import os
from contextlib import contextmanager
from tempfile import NamedTemporaryFile

from hgpaktool.utils import padding, parse_manifest, roundup


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


@contextmanager
def tempfile_with_data(data: str):
    """Simple wrapper around the NamedTemporaryFile to write it with data but allow the file to be read."""
    tmp = NamedTemporaryFile("w", delete=False)
    tmp.write(data)
    tmp.close()
    yield tmp
    os.unlink(tmp.name)


def test_parse_manifest():
    # Posix path
    with tempfile_with_data("test/path.mbin\r\n") as tmp:
        assert parse_manifest(tmp.name) == ["test/path.mbin"]
    with tempfile_with_data("test/path.mbin\r\ntest/path2.mbin\r\n") as tmp:
        assert parse_manifest(tmp.name) == ["test/path.mbin", "test/path2.mbin"]
    # Windows path
    with tempfile_with_data("test\\path.mbin\r\n") as tmp:
        assert parse_manifest(tmp.name) == ["test/path.mbin"]
    with tempfile_with_data("test\\path.mbin\r\ntest\\path2.mbin\r\n") as tmp:
        assert parse_manifest(tmp.name) == ["test/path.mbin", "test/path2.mbin"]
    # Mix
    with tempfile_with_data("test/path.mbin\r\ntest\\path2.mbin\r\n") as tmp:
        assert parse_manifest(tmp.name) == ["test/path.mbin", "test/path2.mbin"]
