import os.path as op
import sys
from logging import NullHandler, getLogger
from typing import Optional, Union, cast

from hgpaktool.constants import Compression, CompressionLiteral
from hgpaktool.oodle import OodleCompressor, OodleDecompressionError
from hgpaktool.os_funcs import OSCONST

# Try import both lz4 and zstd.
# zstd is only required for windows, and lz4 is only required for mac, so if either fails, don't break
# immediately, since we may not need it. An error will already be raised by the api if attempting to read a
# file without the required libraries installed.
try:
    import zstandard as zstd
except ModuleNotFoundError:
    pass

try:
    import lz4.block
except ModuleNotFoundError:
    pass


logger = getLogger(__name__)
logger.addHandler(NullHandler())


class Compressor:
    def __init__(self, compression: Union[Compression, CompressionLiteral] = Compression.ZSTD):
        self.compression = compression
        if self.compression == Compression.ZSTD:
            # TEMP fix for decompression. Won't work for compression.
            self.compressor = zstd.ZstdDecompressor()
            self.decompressed_chunk_size = 0x10000
            self._decompress_func = self._decompress_windows
        elif self.compression == Compression.LZ4:
            self.compressor = lz4.block
            self.decompressed_chunk_size = 0x20000
            self._decompress_func = self._decompress_mac
        else:
            self.compressor = OodleCompressor(op.join(op.dirname(__file__), "lib", OSCONST.LIB_NAME))
            self.decompressed_chunk_size = 0x20000
            self._decompress_func = self._decompress_switch

    def compress(self, buffer: memoryview) -> bytes:
        if self.compression == Compression.ZSTD:
            raise NotImplementedError("Recompression not supported on windows yet.")
            # self.compressor = zstd.ZstdCompressor()
            # return self.compressor.compress(
            #     buffer,
            #     store_size=False,
            # )
        elif self.compression == Compression.LZ4:
            self.compressor = cast(lz4.block, self.compressor)
            return self.compressor.compress(
                buffer,
                store_size=False,
            )
        else:
            self.compressor = cast(OodleCompressor, self.compressor)
            return self.compressor.compress(buffer.tobytes("A"), self.decompressed_chunk_size)

    def _decompress_windows(self, data: bytes) -> Optional[bytes]:
        self.compressor = cast(zstd.ZstdDecompressor, self.compressor)
        try:
            return self.compressor.decompress(data, max_output_size=self.decompressed_chunk_size)
        except zstd.ZstdError:
            if len(data) == self.decompressed_chunk_size:
                # In this case the block was just not compressed. Return it.
                return data
            else:
                if data[:2] == b"\x8c\x0a":
                    logger.error("Provided pak is from the switch. Please add `--platform switch`")
                    sys.exit(1)
                logger.exception("Error decompressing a chunk:")
                return None

    def _decompress_mac(self, data: bytes) -> Optional[bytes]:
        self.compressor = cast(lz4.block, self.compressor)
        try:
            return self.compressor.decompress(data, uncompressed_size=self.decompressed_chunk_size)
        except lz4.block.LZ4BlockError:
            if len(data) == self.decompressed_chunk_size:
                # In this case the block was just not compressed. Return it.
                return data
            else:
                if data[:2] == b"\x8c\x0a":
                    logger.error("Provided pak is from the switch. Please add `--platform switch`")
                    sys.exit(1)
                logger.exception("Error decompressing a chunk:")
                return None

    def _decompress_switch(self, data: bytes) -> Optional[bytes]:
        self.compressor = cast(OodleCompressor, self.compressor)
        try:
            return self.compressor.decompress(data, len(data), self.decompressed_chunk_size)
        except OodleDecompressionError:
            if len(data) == self.decompressed_chunk_size:
                # In this case the block was just not compressed. Return it.
                return data
            else:
                logger.exception("Error decompressing a chunk:")
                return None

    def decompress(self, data: bytes) -> Optional[bytes]:
        """Decompress the provided data blob. This will be decompressed based on the platform specified for
        this compressor object."""
        return self._decompress_func(data)
