__author__ = "monkeyman192"
__version__ = "1.0.4"

import argparse
import array
from collections import namedtuple, defaultdict
from contextvars import ContextVar
from enum import Enum
import fnmatch
from functools import lru_cache
import hashlib
from io import BytesIO, SEEK_SET, SEEK_CUR, SEEK_END
import json
import os
import os.path as op
import pathlib
import platform
import shutil
import struct
import sys
import time
from typing import Iterator, Optional

# Unused for now, but if it's confirmed that switch still uses oodle this can be
# used if switch paks are being decompressed.
from OodleCompressor import OodleCompressor, OodleDecompressionError
from utils import OSCONST

# Try import both lz4 and zstd.
# zstd is only required for windows, and lz4 is only required for mac, so if either fails, don't break
# immediately, only break once we know what platform we are targeting.

zstd_imported = False
try:
    import zstandard as zstd
    zstd_imported = True
except ModuleNotFoundError:
    pass

lz4_imported = False
try:
    import lz4.block
    lz4_imported = True
except ModuleNotFoundError:
    pass

try:
    import json5 as json
except ModuleNotFoundError:
    pass


FILEINFO = namedtuple("FILEINFO", ["file_hash", "start_offset", "decompressed_size"])
FILEINFO_FMT = "<16s2Q"
CHUNKINFO = namedtuple("CHUNKINFO", ["size", "offset"])

# The game decompresses chunks to this size blocks (64kb)
DECOMPRESSED_CHUNK_SIZE = 0x10000
CLEAN_BYTES = b"\x00" * DECOMPRESSED_CHUNK_SIZE


ctx_verbose: ContextVar[bool] = ContextVar("verbose", default=False)
ctx_verbose.set(False)

ctx_dryrun: ContextVar[bool] = ContextVar("dryrun", default=False)
ctx_dryrun.set(False)

ctx_upper: ContextVar[bool] = ContextVar("upper", default=False)
ctx_upper.set(False)


EMPTY_EXML_DATA = """<?xml version="1.0" encoding="utf-8"?>\n<Data />"""


class InvalidFileException(Exception):
    pass


class Platform(Enum):
    WINDOWS = "windows"
    MAC = "mac"
    SWITCH = "switch"


platform_map = defaultdict(
    lambda: "windows",
    {
        "Windows": "windows",
        "Darwin": "mac",
    },
)


def reqChunkBytes(chunk_size: int):
    """ Get the minimum required number of bytes which is a multiple of 0x10
    which can fit the specified number of bytes in the chunk."""
    return 0x10 * determine_bins(chunk_size)


def determine_bins(num_bytes: int, bin_size: int = 0x10):
    """ Determine the number of bins required to hold the requested number of bytes. """
    return (num_bytes + bin_size - 1) // bin_size


def roundup(x: int):
    """ Round up a number to the nearest 0x10 byte boundary. """
    # x >> 4 << 4 will round down to the nearest 0x10.
    # Then check if the number was higher by bitwise and-ing 0xF.
    # If it is, then add the 0x10.
    return (x >> 4 << 4) + ((x & 0xF) and 0x10)


def padding(x: int):
    """ Determine the number of bytes required to pad the value to a 0x10
    byte boundary """
    return (0x10 - (x & 0xF)) & 0xF


class Compressor():
    def __init__(self, platform: Platform = Platform.WINDOWS):
        self.platform = platform
        if self.platform == Platform.WINDOWS:
            # TEMP fix for decompression. Won't work for compression.
            self.compressor = zstd.ZstdDecompressor()
        elif self.platform == Platform.MAC:
            self.compressor = lz4.block
        else:  # SWITCH
            self.compressor = OodleCompressor(
                op.join(op.dirname(__file__), "lib", OSCONST.LIB_NAME)
            )

    def compress(self, buffer: memoryview) -> bytes:
        if self.platform == Platform.WINDOWS or self.platform == Platform.MAC:
            return self.compressor.compress(
                buffer,
                store_size=False,
            )
        else:
            return self.compressor.compress(
                buffer.tobytes("A"),
                DECOMPRESSED_CHUNK_SIZE
            )

    def decompress(self, data: bytes) -> bytes:
        if self.platform == Platform.WINDOWS:
            try:
                return self.compressor.decompress(
                        data,
                        max_output_size=DECOMPRESSED_CHUNK_SIZE
                    )
            except zstd.ZstdError:
                if len(data) == DECOMPRESSED_CHUNK_SIZE:
                    # In this case the block was just not compressed. Return it.
                    return data
                else:
                    if data[:2] == b"\x8C\x0A":
                        print("Provided pak is from the switch. Please add `--platform switch`")
                        sys.exit(1)
                    print("Error decompressing a chunk:")
                    raise
        elif self.platform == Platform.MAC:
            try:
                return self.compressor.decompress(
                        data,
                        uncompressed_size=DECOMPRESSED_CHUNK_SIZE
                    )
            except lz4.block.LZ4BlockError:
                if len(data) == DECOMPRESSED_CHUNK_SIZE:
                    # In this case the block was just not compressed. Return it.
                    return data
                else:
                    if data[:2] == b"\x8C\x0A":
                        print("Provided pak is from the switch. Please add `--platform switch`")
                        sys.exit(1)
                    print("Error decompressing a chunk:")
                    raise
        else:
            try:
                return self.compressor.decompress(
                        data,
                        len(data),
                        DECOMPRESSED_CHUNK_SIZE
                    )
            except OodleDecompressionError:
                if len(data) == DECOMPRESSED_CHUNK_SIZE:
                    # In this case the block was just not compressed. Return it.
                    return data
                else:
                    print("Error decompressing a chunk:")
                    raise


class File():
    __slots__ = ("offset", "size", "path", "_in_chunks")

    def __init__(self, offset: int, size: int, path: str):
        self.offset = offset
        self.size = size
        self.path = path
        self._in_chunks = None

    @property
    def in_chunks(self) -> tuple[int]:
        """ Determine which chunks the file is contained in. """
        if self._in_chunks is None:
            if self.offset % DECOMPRESSED_CHUNK_SIZE == 0:
                start_chunk = determine_bins(self.offset, DECOMPRESSED_CHUNK_SIZE)
            else:
                start_chunk = determine_bins(self.offset, DECOMPRESSED_CHUNK_SIZE) - 1
            end_chunk = determine_bins(self.offset + self.size, DECOMPRESSED_CHUNK_SIZE) - 1
            self._in_chunks = (start_chunk, end_chunk)
        return self._in_chunks

    @property
    def first_chunk_offset(self):
        """ The offset within the first chunk that the file starts at. """
        return self.offset % DECOMPRESSED_CHUNK_SIZE
    
    @property
    def last_chunk_offset_end(self):
        """ The offset within the last chunk where the file ends. """
        return (self.offset + self.size) % DECOMPRESSED_CHUNK_SIZE

    def __str__(self):
        return f"File: {self.path}: Offset: 0x{self.offset:X}, Size: 0x{self.size:X}, In chunks: {self.in_chunks}"

    def __repr__(self):
        return str(self)


class FixedBuffer(BytesIO):
    def __init__(self, main_buffer: BytesIO, compress: bool = False,
                 compressor: Optional[Compressor] = None):
        # NOTE: compressor can't ever actually be None. I need to clean this up.
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
        """ Add the provided bytes to the buffer.
        The amount of bytes passed in will never be more than 0x20000.
        If the amount fills the buffer then flush and prefill the next buffer.
        """
        data_size = len(data)
        written_bytes = self.write(data[:self.remaining_bytes])
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
        """ Write the data in the current buffer into the main buffer. """
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
        """ Clear the current buffer. """
        self.seek(0)
        self.write(CLEAN_BYTES)
        self.seek(0)


def chunked_file_reader(fpaths: list[str]) -> Iterator[bytes]:
    """ Yield chunks of size up to 0x20000 bytes from a file. """
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


class HGPakHeader():
    def __init__(self):
        self.version = None
        self.fileCount = 0
        self.chunk_count = 0
        self.is_compressed = False
        self.dataOffset = 0

    def read(self, fobj):
        fobj.seek(0, SEEK_SET)
        if struct.unpack('5s', fobj.read(5))[0] != b"HGPAK":
            raise InvalidFileException(f"{fobj.name} does not appear to be a valid HGPAK file.")
        fobj.seek(8, SEEK_SET)
        self.version, self.fileCount, self.chunk_count, self.is_compressed, self.dataOffset = (
            struct.unpack('<QQQ?7xQ', fobj.read(0x28))
        )

        if ctx_verbose.get() is True:
            print(f"Pak file {fobj.name} headers:")
            print(str(self))

    def __str__(self):
        return (
            f"HGPak Header:\n"
            f" Version {self.version}\n"
            f" Files: {self.fileCount}\n"
            f" Chunks: {self.chunk_count}\n"
            f" is Compressed: {self.is_compressed}\n"
            f" Data offset: 0x{self.dataOffset:X}\n"
        )


class HGPakFileIndex():
    def __init__(self):
        self.fileInfo: list[FILEINFO] = []
        self.final_offset = 0
        self.final_offset_size = 0

    def read(self, fileCount: int, fobj):
        for _ in range(fileCount):
            finf = FILEINFO(*struct.unpack(FILEINFO_FMT, fobj.read(0x20)))
            self.fileInfo.append(finf)
            if finf.start_offset > self.final_offset:
                self.final_offset = finf.start_offset
                self.final_offset_size = finf.decompressed_size

    def write(self, fobj):
        for finf in self.fileInfo:
            fobj.write(struct.pack(FILEINFO_FMT, *finf._asdict().values()))


class HGPakChunkIndex():
    def __init__(self):
        self.chunk_sizes: list[int] = []
        self.chunk_offset: list[int] = []

    def read(self, chunk_count: int, fobj):
        self.chunk_sizes = struct.unpack(f"<{chunk_count}Q", fobj.read(8 * chunk_count))


class HGPakFile():
    def __init__(self, fobj, compressor: Compressor):
        self.fobj = fobj
        self.header: HGPakHeader = HGPakHeader()
        self.fileIndex: HGPakFileIndex = HGPakFileIndex()
        self.chunkIndex: HGPakChunkIndex = HGPakChunkIndex()
        self.files: dict[str, File] = {}
        self.filenames = list[str]
        self.compressor = compressor

    @property
    def total_decompressed_size(self):
        return self.fileIndex.final_offset + self.fileIndex.final_offset_size

    def read(self):
        self.header.read(self.fobj)
        self.fileIndex.read(self.header.fileCount, self.fobj)
        if self.header.is_compressed is False:
            # We only need to read the filename data and then return.
            self.fobj.seek(self.header.dataOffset, SEEK_SET)
            filename_data = self.fobj.read(
                self.fileIndex.fileInfo[0].decompressed_size
            )
            self.filenames = [
                x.decode() for x in filename_data[
                    :self.fileIndex.fileInfo[0].decompressed_size
                ].rstrip(b"\x0D\x0A").split(b"\x0D\x0A")
            ]
            for i, fname in enumerate(self.filenames):
                if fname:
                    finf = self.fileIndex.fileInfo[i + 1]
                    self.files[fname] = File(
                        finf.start_offset,
                        finf.decompressed_size,
                        fname
                    )
            return
        # Determine the expected number of chunks and see if this matches
        found_chunk_count = determine_bins(
            self.total_decompressed_size,
            DECOMPRESSED_CHUNK_SIZE
        )
        if found_chunk_count != self.header.chunk_count:
            if ctx_verbose.get() is True:
                print(
                    f"chunk mismatch. Found: {found_chunk_count}, "
                    f"expected: {self.header.chunk_count}"
                )
        if self.header.is_compressed:
            self.chunkIndex.read(self.header.chunk_count, self.fobj)
        # Finally, we should now be at the start of the compressed data.
        # Instead of reading it all into a buffer. We'll just jump over to
        # get the offsets for easier reading later.
        self.fobj.seek(self.header.dataOffset, SEEK_SET)
        for i, size in enumerate(self.chunkIndex.chunk_sizes):
            # Set the offset.
            self.chunkIndex.chunk_offset.append(self.fobj.tell())
            # Then jump forward the required amount.
            if i != self.header.chunk_count:
                self.fobj.seek(reqChunkBytes(size), SEEK_CUR)

        # Determine how many chunks to decompress to read the filenames.
        chunks_for_filenames = determine_bins(
            self.fileIndex.fileInfo[0].decompressed_size,
            DECOMPRESSED_CHUNK_SIZE
        )

        # Decompress these chunks to read the filenames.
        first_chunks = b""
        for i in range(chunks_for_filenames):
            first_chunks += self.decompress_chunk(i)
        self.filenames = [
            x.decode() for x in first_chunks[
                :self.fileIndex.fileInfo[0].decompressed_size
            ].rstrip(b"\x0D\x0A").split(b"\x0D\x0A")
        ]
        assert len(self.filenames) == self.header.fileCount - 1, "file count mismatch"
        for i, fname in enumerate(self.filenames):
            if fname:
                finf = self.fileIndex.fileInfo[i + 1]
                self.files[fname] = File(
                    finf.start_offset - self.header.dataOffset,
                    finf.decompressed_size,
                    fname
                )

    @lru_cache(maxsize = 128)
    def decompress_chunk(self, chunkIdx: int):
        self.fobj.seek(self.chunkIndex.chunk_offset[chunkIdx], SEEK_SET)
        chunk_size = self.chunkIndex.chunk_sizes[chunkIdx]
        return self.compressor.decompress(self.fobj.read(chunk_size))

    def unpack(
        self,
        out_dir: str = "EXTRACTED",
        filters: Optional[list[str]] = None,
        file_list: Optional[list[str]] = None,
    ) -> int:
        i = 0
        if filters is not None:
            files = set()
            for filter_ in filters:
                files.update(fnmatch.filter(self.files, filter_))
        elif file_list is not None:
            files = set(self.files) & set(file_list)
        else:
            files = self.files
        if len(files) == 0:
            return 0
        if self.header.is_compressed:
            func = self._extract_file_compressed
        else:
            func = self._extract_file_uncompressed
        for fpath in files:
            func(fpath, out_dir)
            i += 1
        return i

    def _extract_file_compressed(self, fpath: str, out_dir: str):
        # First, get the file info.
        finf = self.files.get(fpath)
        if not finf:
            raise FileNotFoundError("The specified file path doesn't exist in this pak")
        start_chunk, end_chunk = finf.in_chunks
        first_off = finf.first_chunk_offset
        last_off = finf.last_chunk_offset_end
        _data = BytesIO()
        if start_chunk == end_chunk:
            # The data is contained entirely within the same chunk.
            decompressed = self.decompress_chunk(start_chunk)
            if decompressed is None:
                print(f"There was an issue decompressing chunk {start_chunk}")
                print(f"Unable to extract file: {fpath}")
                return
            if last_off:
                _data.write(decompressed[first_off:last_off])
            else:
                _data.write(decompressed[first_off:])
        else:
            for chunk_idx in range(start_chunk, end_chunk + 1):
                decompressed = self.decompress_chunk(chunk_idx)
                if decompressed is None:
                    print(f"There was an issue decompressing chunk {chunk_idx}")
                    print(f"Unable to extract file: {fpath}")
                    return
                if chunk_idx == start_chunk:
                    _data.write(decompressed[first_off:])
                elif chunk_idx == end_chunk:
                    if not last_off:
                        _data.write(decompressed[:])
                    else:
                        _data.write(decompressed[:last_off])
                else:
                    _data.write(decompressed)
        if _data.tell() != finf.size:
            print(f"There was an error extracting the file {fpath}")
            print(f"File info details: {finf} {first_off} {last_off}")
        # Now write the file out.
        _export_path, fname = op.split(fpath)
        dir_ = op.join(out_dir, _export_path)
        if ctx_upper.get() is True:
            dir_ = dir_.upper()
            fname = fname.upper()
        if dir_:
            os.makedirs(dir_, exist_ok=True)
        if not ctx_dryrun.get():
            with open(op.join(dir_, fname), "wb") as f:
                f.write(_data.getbuffer())

    def _extract_file_uncompressed(self, fpath: str, out_dir: str):
        finf = self.files.get(fpath)
        if not finf:
            raise FileNotFoundError("The specified file path doesn't exist in this pak")
        self.fobj.seek(finf.offset)
        # Now write the file out.
        _export_path, fname = op.split(fpath)
        dir_ = op.join(out_dir, _export_path)
        if ctx_upper.get() is True:
            dir_ = dir_.upper()
            fname = fname.upper()
        if dir_:
            os.makedirs(dir_, exist_ok=True)
        if not ctx_dryrun.get():
            with open(op.join(dir_, fname), "wb") as f:
                f.write(self.fobj.read(finf.size))

    def compress(self):
        """ Compress an archive. """
        pass

    def decompress(self):
        """ Decompress an archive. """
        pass


def pack(files: list[str], root_directory: str, filename_hash: bytes,
         compress: bool = False, compressor: Optional[Compressor] = None):
    """ Add a number of files to the archive"""
    # First, let's create a buffer which will contain the data.
    buffer = BytesIO()
    # Then we'll write the header into it.
    # This will be incomplete as we'll need to fill in some data later.
    # The missing data will be the chunk_count and the data_offset as these
    # both require all the data to be known before they can be written.
    # We'll also not write the file count since this will be determined when we
    # actually walk over any directories passed in.
    buffer.write(struct.pack('<5s3xQQQ?7xQ', b"HGPAK", 2, 0, 0, compress, 0))

    hashes: list[bytes] = []

    # Let's get information about all the files.
    # This will be the size and the path relative to the root_directory.
    # While we are doing this we can create the filepath data as bytes as
    # this will be considered the first "file" in the archive.
    file_sizes = array.array("Q")
    file_offsets = array.array("Q")
    filepath_data = b""
    # keep track of the full paths so that we may load them from disk to read.
    fullpaths: list[str] = []
    # The list of relative paths. These are what will be written into the paths
    # chunk and what will be hashed to write the hashes.
    rel_paths: list[bytes] = []
    for _fpath in files:
        # If the path is a directory, we'll need to loop over it to get all the
        # files within the directory.
        # Add these full paths so that we may use this as the new path list
        # to avoid some extra complexity in the logic.
        if op.isdir(_fpath):
            for root, _, files_ in os.walk(_fpath):
                for fname in files_:
                    fullpath = op.join(root, fname)
                    fullpaths.append(fullpath)
                    file_sizes.append(os.stat(fullpath).st_size)
                    relpath = op.relpath(fullpath, root_directory)
                    # On windows the path will have \'s instead of /'s. Fix it.
                    if op.sep == "\\":
                        relpath = pathlib.PureWindowsPath(relpath).as_posix()
                    relpath_bytes = relpath.encode()
                    filepath_data += relpath_bytes + b"\x0D\x0A"
                    rel_paths.append(relpath_bytes)
        else:
            fullpaths.append(_fpath)
            file_sizes.append(os.stat(_fpath).st_size)
            relpath = op.relpath(_fpath, root_directory)
            # On windows the path will have \'s instead of /'s. Fix it.
            if op.sep == "\\":
                relpath = pathlib.PureWindowsPath(relpath).as_posix()
            relpath_bytes = relpath.encode()
            filepath_data += relpath_bytes + b"\x0D\x0A"
            rel_paths.append(relpath_bytes)
    filepath_data_len = len(filepath_data)

    # Hash the file names
    for fname in rel_paths:
        hashes.append(hashlib.md5(fname).digest())

    # Aggregate the above data to determine the offsets and total size of the
    # uncompressed data.
    curr_total_data = 0
    curr_total_data += roundup(filepath_data_len)
    for fsize in file_sizes:
        file_offsets.append(curr_total_data)
        curr_total_data += roundup(fsize)

    # Now that we have the total uncompressed data, we may write the total
    # number of chunks as this will be equal to the total data size chunked
    # into 0x20000 bins.
    chunk_count = determine_bins(curr_total_data, DECOMPRESSED_CHUNK_SIZE)

    # Now that we know the total chunk count and file count, we know how big the
    # "pre-data" data is.
    data_offset = 0x30 + 0x20 * (len(file_sizes) + 1) + compress * 0x8 * chunk_count
    # Also add padding so that the data always starts at an offset which is a
    # multiple of 0x10
    extra_padding = padding(data_offset)
    data_offset += extra_padding

    # Now write the file index data, and reserve the chunk index data.
    buffer.write(struct.pack("16s", filename_hash))
    buffer.write(struct.pack("<QQ", data_offset, filepath_data_len))
    for i, fsize in enumerate(file_sizes):
        buffer.write(struct.pack("16s", hashes[i]))
        buffer.write(struct.pack("<QQ", file_offsets[i] + data_offset, fsize))
    # Reserve space for the compressed chunk sizes if the file is compressed.
    chunk_index_offset = buffer.tell()
    if compress:
        buffer.write(b"\x00" * (8 * chunk_count + extra_padding))

    # Write the file count into the header.
    buffer.seek(0x10, SEEK_SET)
    buffer.write(struct.pack("<Q", len(file_sizes) + 1))
    # And the chunk count.
    buffer.seek(0x18, SEEK_SET)
    buffer.write(struct.pack('<Q', chunk_count))
    # And the data offset.
    buffer.seek(0x28, SEEK_SET)
    buffer.write(struct.pack('<Q', data_offset))

    # We now have everything in place (finally!) to start reading the input
    # files and potentially compressing them.

    buffer.seek(0, SEEK_END)

    sub_buffer = FixedBuffer(buffer, compress, compressor)

    # First, write the filename buffer into the temp_buffer.
    req_chunks = determine_bins(filepath_data_len, DECOMPRESSED_CHUNK_SIZE)
    for i in range(req_chunks):
        sub_buffer.add_bytes(filepath_data[DECOMPRESSED_CHUNK_SIZE * i: DECOMPRESSED_CHUNK_SIZE * (i + 1)])
    # Add the padding bytes for the filepath data
    sub_buffer.add_bytes(b"\x00" * padding(filepath_data_len))

    for _data in chunked_file_reader(fullpaths):
        sub_buffer.add_bytes(_data)
    # Finally, call write_to_main_buffer to flush the last block to the file.
    sub_buffer.write_to_main_buffer()

    if compress:
        buffer.seek(chunk_index_offset, SEEK_SET)
        # Get the compressed block sizes and write to the chunk info section.
        for chunk_size in sub_buffer.compressed_block_sizes:
            buffer.write(struct.pack("<Q", chunk_size))

    return buffer


def should_unpack(filenames: list[str]) -> bool:
    return (
        all([x.lower().endswith(".pak") for x in filenames])
        or (len(filenames) == 1 and op.isdir(filenames[0]))
        or (len(filenames) == 1 and filenames[0].lower().endswith(".json"))
    )

def make_filename_unixhidden(path: str):
    """Add a dot at the beginning of a filename, respecting its path

    Args:
        path (str): The absolute or relative file path.

    Returns:
        hidden (str): The hidden filename version."""

    return op.join(op.dirname(path), "." + op.basename(path))

if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        prog=f"HGPAKtool ({__version__})",
        description="A tool for handling HG's custom .pak format for mac and switch",
    )
    parser.add_argument(
        "-L",
        "--list",
        action="store_true",
        default=False,
        help="Generate a list of files contained within the pak file.",
    )
    parser.add_argument(
        "-N",
        "--nocontents",
        action="store_true",
        default=False,
        help="[DEPRECATED] Store the contents of a .pak in a file for recompression",
    )
    parser.add_argument(
        "-p",
        "--plain",
        action="store_true",
        default=False,
        help=(
            "Whether to output any generation informational files in a "
            "simplified format"
        )
    )
    parser.add_argument(
        "-C",
        "--contents",
        action="store_true",
        default=False,
        help="Store the contents of a .pak in a file for recompression",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        default=False,
        help="Log extra info when (un)packing archives",
    )
    parser.add_argument(
        "--platform",
        choices=("windows", "mac", "switch"),
        default=platform_map[platform.system()],
        const=platform_map[platform.system()],
        nargs="?",
        help="The platform to unpack the files for. Default: %(default)s.",
    )
    parser.add_argument(
        "-Z",
        "--compress",
        action="store_true",
        help="Whether or not to compress the provided files."
    )
    parser.add_argument(
        "-O",
        "--output",
        required=False,
        help=(
            "The directory to place extracted files in. If not provided, falls back to a folder called "
            "'EXTRACTED' in the current directory."
        )
    )
    parser.add_argument(
        "-f",
        "--filter",
        action="append",
        help="A glob pattern which can be used to filter out the files which are to be extracted."
    )
    parser.add_argument(
        "-j",
        "--json",
        help=(
            "The path to a json file which can be used to indicate the files to be unpacked.\n"
            "The keys to the json are the paths (either relative or absolute) to the pak's that are to be "
            "extracted, and the values are the files within these pak's to extract.\n"
            "If the pak paths are relative, then a 'filename' argument MUST be passed which is the root "
            "directory of the provided pak names."
        )
    )
    parser.add_argument(
        "--dryrun",
        action="store_true",
        default=False,
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--upper",
        action="store_true",
        default=False,
        help="If provided, extracted filenames will be converted to UPPERCASE."
    )
    pup_group = parser.add_mutually_exclusive_group()  # pup = pack/unpack
    pup_group.add_argument(
        "-U",
        "--unpack",
        action="store_true",
        default=True,
        help="Unpack the files from the provided pak files.",
    )
    pup_group.add_argument(
        "-P",
        "--pack",
        action="store_true",
        default=False,
        help="Pack the provided files into a pak file.",
    )
    pup_group.add_argument(
        "-R",
        "--repack",
        action="store_true",
        default=False,
        help="Repack the files for a given vanilla pak name."
    )
    parser.add_argument(
        "filenames",
        nargs="+",
        help=(
            "The file(s) to pack or unpack. If this is a list of pak files or a directory, then it will be "
            "assumed that the files need to be unpacked.\nIf the filename is a single json file in the same "
            "format as produced by the -L flag, then it will also be unpacked as per the listed pak files "
            "and listed contents."
        )
    )

    args = parser.parse_args()
    filenames = args.filenames
    if should_unpack(filenames):
        # All the files provided are pak files, so decompress them unless we
        # have been asked to repack them
        if args.repack:
            mode = "repack"
        else:
            mode = "unpack"
    else:
        mode = "pack"

    if args.verbose is True:
        ctx_verbose.set(True)

    if args.dryrun is True:
        ctx_dryrun.set(True)

    if args.upper is True:
        ctx_upper.set(True)

    plat = Platform(args.platform)
    if plat == Platform.WINDOWS:
        if zstd_imported is False:
            print("You need to install zstandard for this code to work. Please run `pip install zstandard`")
            sys.exit(1)
    elif plat == Platform.MAC:
        if lz4_imported is False:
            print("You need to install lz4 for this code to work. Please run `pip install lz4`")
            sys.exit(1)
    elif plat == Platform.SWITCH:
        # Decompressed chunk size on switch is 128kb
        DECOMPRESSED_CHUNK_SIZE = 0x20000
        CLEAN_BYTES = b"\x00" * DECOMPRESSED_CHUNK_SIZE

    compressor = Compressor(plat)

    if mode == "unpack":
        output = op.abspath(args.output or "EXTRACTED")
        if not op.exists(output) and not args.list:
            os.makedirs(output, exist_ok=True)
        t1 = time.time()
        pack_count = 0
        file_count = 0
        filename_data: dict[str, list[str]] = {}

        json_file: Optional[str] = None
        if args.json:
            json_file = args.json
        elif len(filenames) == 1 and filenames[0].lower().endswith(".json"):
            json_file = filenames[0]

        if json_file is not None:
            root_dir = None
            if len(filenames) > 1:
                print("Cannot unpack with json from multiple directories. Please only provide one directory")
                sys.exit(1)
            elif len(filenames) == 1:
                if op.exists(filenames[0]) and op.isdir(filenames[0]):
                    root_dir = filenames[0]
            # In this case, we got a json file which contains a list of paks to unpack from.
            with open(json_file, "r") as f:
                json_data = json.load(f)
            for pak_path, req_contents in json_data.items():
                abs_pak_path = pak_path
                if not op.isabs(pak_path):
                    if root_dir is not None:
                        abs_pak_path = op.join(root_dir, pak_path)
                    else:
                        print(f"Cannot extract {pak_path} as it's a relative path. "
                              "Either provide the absolute path, or provide the root directory as the "
                              "'filename' argument.")
                        continue
                if not abs_pak_path.lower().endswith(".pak"):
                    print(f"Skipping {pak_path}: Not a valid file to extract.")
                    continue
                with open(abs_pak_path, "rb") as pak:
                    print(f"Reading {op.basename(abs_pak_path)}")
                    f = HGPakFile(pak, compressor)
                    f.read()
                    file_count += f.unpack(output, None, req_contents)
                pack_count += 1
        else:
            for filename in filenames:
                if op.isdir(filename):
                    for fname in os.listdir(filename):
                        if not fname.lower().endswith(".pak"):
                            print(f"{fname} is not a valid path to extract.")
                            continue
                        print(f"Reading {fname}")
                        with open(op.join(filename, fname), "rb") as pak:
                            f = HGPakFile(pak, compressor)
                            f.read()
                            # generate a list of the contained files
                            fullpath = op.join(op.realpath(filename), fname)
                            filename_data[fullpath] = f.filenames
                            if not args.list:
                                file_count += f.unpack(output, args.filter)
                        pack_count += 1
                else:
                    with open(filename, "rb") as pak:
                        f = HGPakFile(pak, compressor)
                        f.read()
                        # generate a list of the contained files
                        filename_data[op.realpath(filename)] = f.filenames
                        if not args.list:
                            file_count += f.unpack(output, args.filter)
                    pack_count += 1

        if args.list:
            if args.plain:
                with open("filenames.txt", "w") as f:
                    for pakname, filenames in filename_data.items():
                        f.write(f"Listing {pakname}\n")
                        for fname in filenames:
                            f.write(fname + "\n")
            else:
                with open("filenames.json", "w") as f:
                    f.write(json.dumps(filename_data, indent=2))
            print(f"Listed contents of {pack_count} .pak's in {time.time() - t1:3f}s")
        elif args.contents:
            for pakname, filenames in filename_data.items():
                with open(f"{make_filename_unixhidden(pakname)}.contents", "w") as f:
                    f.write(
                        json.dumps({"filenames": filenames, "root_dir": output})
                    )
        else:
            print(f"Unpacked {file_count} files from {pack_count} .pak's in {time.time() - t1:3f}s")
    elif mode == "pack":
        output = args.output or "hgpak.pak"
        pak_hash = hashlib.md5(output.encode()).digest()
        # Need to do some processing of the filenames/paths we are provided.
        # If the paths provided are absolute we will assume that they are from
        # the batch script. In this case the "root" directory will be the parent
        # directory of the first file
        if op.isabs(filenames[0]):
            root_dir = op.dirname(filenames[0])
        else:
            # Otherwise, if the paths are relative, the root path will be the
            # folder up from this...
            root_dir = op.abspath(op.dirname(op.dirname(__file__)))

        data = pack(
            [op.abspath(fname) for fname in filenames],
            root_dir,
            pak_hash,
            args.compress,
            compressor,
        )
        with open(output, "wb") as f_out:
            f_out.write(data.getbuffer())
    else:
        with open(op.join(op.dirname(__file__), "..", "filename_hashes.json"), "r") as f:
            filename_hashes: dict[str, str] = json.load(f)
        # "repack" mode
        for pakname in filenames:
            pakname = op.basename(pakname)
            if pakname in filename_hashes:
                pak_hash = bytes.fromhex(filename_hashes[pakname])
            else:
                pak_hash = hashlib.md5(pakname.encode()).digest()
            output = args.output or pakname
            with open(f"{make_filename_unixhidden(pakname)}.contents", "r") as f:
                _contents = json.loads(f.read())
                pak_contents = _contents["filenames"]
                root_dir = _contents["root_dir"]
            data = pack(
                [op.join(root_dir, fname) for fname in pak_contents],
                root_dir,
                pak_hash,
                args.compress,
                compressor,
            )

            # Rename the original file.
            if not op.exists(f"{pakname}.bak"):
                shutil.move(pakname, f"{pakname}.bak")
            with open(pakname, "wb") as f_out:
                f_out.write(data.getbuffer())
