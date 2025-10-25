import os.path as op
import sys
from typing import Literal, Union, cast

from hgpaktool.constants import Platform
from hgpaktool.oodle import OodleCompressor, OodleDecompressionError
from hgpaktool.os_funcs import OSCONST

# Try import both lz4 and zstd.
# zstd is only required for windows, and lz4 is only required for mac, so if either fails, don't break
# immediately, only break once we know what platform we are targeting.

try:
    import zstandard as zstd
except ModuleNotFoundError:
    pass

try:
    import lz4.block
except ModuleNotFoundError:
    pass


class Compressor:
    def __init__(self, platform: Union[Platform, Literal["windows", "mac", "switch"]] = Platform.WINDOWS):
        self.platform = platform
        if self.platform == Platform.WINDOWS:
            # TEMP fix for decompression. Won't work for compression.
            self.compressor = zstd.ZstdDecompressor()
            self.decompressed_chunk_size = 0x10000
        elif self.platform == Platform.MAC:
            self.compressor = lz4.block
            self.decompressed_chunk_size = 0x20000
        else:  # SWITCH
            self.compressor = OodleCompressor(op.join(op.dirname(__file__), "lib", OSCONST.LIB_NAME))
            self.decompressed_chunk_size = 0x20000

    def compress(self, buffer: memoryview) -> bytes:
        if self.platform == Platform.WINDOWS:
            raise NotImplementedError("Recompression not supported on windows yet.")
            # self.compressor = zstd.ZstdCompressor()
            # return self.compressor.compress(
            #     buffer,
            #     store_size=False,
            # )
        elif self.platform == Platform.MAC:
            self.compressor = cast(lz4.block, self.compressor)
            return self.compressor.compress(
                buffer,
                store_size=False,
            )
        else:
            self.compressor = cast(OodleCompressor, self.compressor)
            return self.compressor.compress(buffer.tobytes("A"), self.decompressed_chunk_size)

    def decompress(self, data: bytes) -> bytes:
        if self.platform == Platform.WINDOWS:
            self.compressor = cast(zstd.ZstdDecompressor, self.compressor)
            try:
                return self.compressor.decompress(data, max_output_size=self.decompressed_chunk_size)
            except zstd.ZstdError:
                if len(data) == self.decompressed_chunk_size:
                    # In this case the block was just not compressed. Return it.
                    return data
                else:
                    if data[:2] == b"\x8c\x0a":
                        print("Provided pak is from the switch. Please add `--platform switch`")
                        sys.exit(1)
                    print("Error decompressing a chunk:")
                    raise
        elif self.platform == Platform.MAC:
            self.compressor = cast(lz4.block, self.compressor)
            try:
                return self.compressor.decompress(data, uncompressed_size=self.decompressed_chunk_size)
            except lz4.block.LZ4BlockError:
                if len(data) == self.decompressed_chunk_size:
                    # In this case the block was just not compressed. Return it.
                    return data
                else:
                    if data[:2] == b"\x8c\x0a":
                        print("Provided pak is from the switch. Please add `--platform switch`")
                        sys.exit(1)
                    print("Error decompressing a chunk:")
                    raise
        else:
            self.compressor = cast(OodleCompressor, self.compressor)
            try:
                return self.compressor.decompress(data, len(data), self.decompressed_chunk_size)
            except OodleDecompressionError:
                if len(data) == self.decompressed_chunk_size:
                    # In this case the block was just not compressed. Return it.
                    return data
                else:
                    print("Error decompressing a chunk:")
                    raise
