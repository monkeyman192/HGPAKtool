from io import BufferedWriter, BytesIO
from typing import Iterator

from hgpaktool.compressors import Compressor
from hgpaktool.constants import CLEAN_BYTES, DECOMPRESSED_CHUNK_SIZE
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


class FixedBuffer(BytesIO):
    def __init__(
        self,
        main_buffer: BufferedWriter,
        compressor: Compressor,
        compress: bool = False,
    ):
        super().__init__(CLEAN_BYTES)
        # The number of bytes remaining until we have a full buffer
        self.remaining_bytes = DECOMPRESSED_CHUNK_SIZE
        self.main_buffer = main_buffer
        self.compress = compress
        self.compressor = compressor
        # If we are compressing the data, keep track of the sizes of the
        # compressed data so we can write it into the TOC once we are done.
        self.compressed_block_sizes = []

    def add_bytes(self, data: bytes):
        """Add the provided bytes to the buffer.
        The amount of bytes passed in will never be more than 0x20000.
        If the amount fills the buffer then flush and prefill the next buffer.
        """
        data_size = len(data)
        written_bytes = self.write(data[: self.remaining_bytes])
        # Subtract of either the amount of bytes added or the number of
        # remaining bytes.
        self.remaining_bytes -= written_bytes
        if self.remaining_bytes == 0:
            self.write_to_main_buffer()
            # Reset the number of remaining bytes
            self.remaining_bytes = DECOMPRESSED_CHUNK_SIZE
        # If we have any extra bytes to write, write them now as the buffer is
        # currently empty.
        if data_size > written_bytes:
            new_written_bytes = self.write(data[written_bytes:])
            self.remaining_bytes -= new_written_bytes

    def write_to_main_buffer(self):
        """Write the data in the current buffer into the main buffer."""
        buffer = self.getbuffer()
        if self.compress:
            compressed_bytes = self.compressor.compress(buffer)
            compressed_size = len(compressed_bytes)
            if compressed_size >= DECOMPRESSED_CHUNK_SIZE:
                # If compression has somehow made it worse, use the original
                # bytes.
                self.main_buffer.write(self.getbuffer())
                compressed_size = DECOMPRESSED_CHUNK_SIZE
            else:
                self.main_buffer.write(compressed_bytes)
                # In this case we'll also need to write some extra bytes which
                # will make the next block we written at an address which is a
                # multiple of 0x10.
                self.main_buffer.write(b"\x00" * padding(compressed_size))
            self.compressed_block_sizes.append(compressed_size)
        else:
            self.main_buffer.write(buffer)
        buffer.release()
        # Once we have written this to the parent buffer, clear ourselves to be
        # ready for the next lot of bytes.
        self.clear()

    def clear(self):
        """Clear the current buffer."""
        self.seek(0)
        self.write(CLEAN_BYTES)
        self.seek(0)
