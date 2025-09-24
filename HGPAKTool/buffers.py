from typing import Iterator

from hgpaktool.constants import DECOMPRESSED_CHUNK_SIZE
from hgpaktool.utils import padding


def chunked_file_reader(fpaths: list[str]) -> Iterator[bytes]:
    """Yield chunks of size up to 0x20000 bytes from a file."""
    for fpath in fpaths:
        with open(fpath, "rb") as f:
            while True:
                data = f.read(DECOMPRESSED_CHUNK_SIZE)
                if not data:
                    break
                data_len = len(data)
                if data_len != DECOMPRESSED_CHUNK_SIZE:
                    # Add on the padding bytes
                    data += b"\x00" * padding(data_len)
                yield data
