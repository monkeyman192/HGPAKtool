from io import BufferedWriter, BytesIO
from typing import Iterator

from hgpaktool.compressors import Compressor
from hgpaktool.utils import padding


def chunked_file_reader(fpaths: list[str], decompressed_chunk_size: int) -> Iterator[bytes]:
    """Yield chunks of size up to decompressed_chunk_size bytes from a file."""
    for fpath in fpaths:
        with open(fpath, "rb") as f:
            while True:
                data = f.read(decompressed_chunk_size)
                if not data:
                    break
                data_len = len(data)
                if data_len != decompressed_chunk_size:
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
        self._clean_bytes = b"\x00" * compressor.decompressed_chunk_size
        super().__init__(self._clean_bytes)
        # The number of bytes remaining until we have a full buffer
        self.main_buffer = main_buffer
        self.compress = compress
        self.compressor = compressor
        self.remaining_bytes = self.compressor.decompressed_chunk_size
        # If we are compressing the data, keep track of the sizes of the
        # compressed data so we can write it into the TOC once we are done.
        self.compressed_block_sizes = []

    @property
    def _decompressed_chunk_size(self) -> int:
        return self.compressor.decompressed_chunk_size

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
            self.remaining_bytes = self.compressor.decompressed_chunk_size
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
            if compressed_size >= self._decompressed_chunk_size:
                # If compression has somehow made it worse, use the original
                # bytes.
                self.main_buffer.write(self.getbuffer())
                compressed_size = self._decompressed_chunk_size
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
        self.write(self._clean_bytes)
        self.seek(0)
