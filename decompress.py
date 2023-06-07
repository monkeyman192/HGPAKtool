from collections import namedtuple
from functools import lru_cache
from io import BytesIO
import os
import os.path as op
import struct
import sys
import time

# Unused for now, but if it's confirmed that switch still uses oodle this can be
# used if switch paks are being decompressed.
# from OodleCompressor import OodleCompressor, OodleDecompressionError
# from utils import OSCONST

try:
    import lz4.block
except ModuleNotFoundError:
    print("You need to install lz4 for this code to work. Please run `pip install lz4`")
    sys.exit(1)


FILEINFO = namedtuple("FILEINFO", ["hash1", "hash2", "startOffset", "decompressed_size"])
CHUNKINFO = namedtuple("CHUNKINFO", ["size", "offset"])

# The game decompresses chunks to this size blocks (128kb)
DECOMPRESSED_CHUNK_SIZE = 0x20000


class InvalidFileException(Exception):
    pass


def reqChunkBytes(chunk_size: int):
    """ Get the minimum required number of bytes which is a multiple of 0x10
    which can fit the specified number of bytes in the chunk."""
    return 0x10 * determine_bins(chunk_size)


def determine_bins(num_bytes: int, bin_size: int = 0x10):
    """ Determine the number of bins required to hold the requested number of bytes. """
    return (num_bytes + bin_size - 1) // bin_size


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


class HGPakHeader():
    def __init__(self):
        self.version = None
        self.fileCount = 0
        self.chunk_count = 0
        self.isCompressed = False
        self.dataOffset = 0

    def read(self, fobj):
        fobj.seek(0)
        if struct.unpack('5s', fobj.read(5))[0] != b"HGPAK":
            raise InvalidFileException
        fobj.seek(8)
        self.version, self.fileCount, self.chunk_count, self.isCompressed, self.dataOffset = (
            struct.unpack('<QQQ?7xQ', fobj.read(0x28))
        )

    def __str__(self):
        return (
            f"HGPak Header:\n"
            f" Version {self.version}\n"
            f" Files: {self.fileCount}\n"
            f" Chunks: {self.chunk_count}\n"
            f" is Compressed: {self.isCompressed}\n"
            f" Data offset: 0x{self.dataOffset:X}\n"
        )


class HGPakFileIndex():
    def __init__(self):
        self.fileInfo: list[FILEINFO] = []
        self.max_offset = 0
        self.max_offset_size = 0

    def read(self, fileCount: int, fobj):
        for _ in range(fileCount):
            finf = FILEINFO(*struct.unpack("<4Q", fobj.read(0x20)))
            self.fileInfo.append(finf)
            if finf.startOffset > self.max_offset:
                self.max_offset = finf.startOffset
                self.max_offset_size = finf.decompressed_size

    def write(self, fobj):
        for finf in self.fileInfo:
            fobj.write(struct.pack("<4Q", *finf._asdict().values()))


class HGPakChunkIndex():
    def __init__(self):
        self.chunk_sizes: list[int] = []
        self.chunk_offset: list[int] = []

    def read(self, chunk_count: int, fobj):
        self.chunk_sizes = struct.unpack(f"<{chunk_count}Q", fobj.read(8 * chunk_count))


class HGPakFile():
    def __init__(self, fobj):
        self.fobj = fobj
        self.header: HGPakHeader = HGPakHeader()
        self.fileIndex: HGPakFileIndex = HGPakFileIndex()
        self.chunkIndex: HGPakChunkIndex = HGPakChunkIndex()
        self.files: dict[str, File] = {}

    @property
    def total_decompressed_size(self):
        return self.fileIndex.max_offset + self.fileIndex.max_offset_size

    def read(self):
        self.header.read(self.fobj)
        if self.header.isCompressed is False:
            raise Exception("Cannot currently load uncompressed paks. Come back later!")
        self.fileIndex.read(self.header.fileCount, self.fobj)
        # Determine the expected number of chunks and see if this matches
        found_chunk_count = determine_bins(
            self.total_decompressed_size,
            DECOMPRESSED_CHUNK_SIZE
        )
        if found_chunk_count != self.header.chunk_count:
            print(
                f"chunk mismatch. Found: {found_chunk_count}, "
                f"expected: {self.header.chunk_count}"
            )
        if self.header.isCompressed:
            self.chunkIndex.read(self.header.chunk_count, self.fobj)
        # Finally, we should now be at the start of the compressed data.
        # Instead of reading it all into a buffer. We'll just jump over to
        # get the offsets for easier reading later.
        self.fobj.seek(self.header.dataOffset)
        for i, size in enumerate(self.chunkIndex.chunk_sizes):
            # Set the offset.
            self.chunkIndex.chunk_offset.append(self.fobj.tell())
            # Then jump forward the required amount.
            if i != self.header.chunk_count:
                self.fobj.seek(reqChunkBytes(size), 1)

        # Determine how many chunks to decompress to read the filenames.
        chunks_for_filenames = determine_bins(
            self.fileIndex.fileInfo[0].decompressed_size,
            DECOMPRESSED_CHUNK_SIZE
        )

        # Compress these chunks to read the filenames.
        first_chunks = b""
        for i in range(chunks_for_filenames):
            first_chunks += self.decompress_chunk(i)
        filenames = first_chunks[:self.fileIndex.fileInfo[0].decompressed_size].split(b"\x0D\x0A")
        # Technically there should be one less, but the filenames end with 0xD 0xA, and so there will be
        # one extra element in the list.
        assert len(filenames) == self.header.fileCount
        for i, fname in enumerate(filenames):
            if fname:
                finf = self.fileIndex.fileInfo[i + 1]
                self.files[fname.decode()] = File(
                    finf.startOffset - self.header.dataOffset,
                    finf.decompressed_size,
                    fname
                )

    @lru_cache(maxsize = 128)
    def decompress_chunk(self, chunkIdx: int):
        self.fobj.seek(self.chunkIndex.chunk_offset[chunkIdx])
        data = self.fobj.read(self.chunkIndex.chunk_sizes[chunkIdx])
        try:
            return lz4.block.decompress(
                data,
                uncompressed_size=DECOMPRESSED_CHUNK_SIZE
            )
        except:
            # Something went wrong. For now just raise it.
            raise

    def extract_all(self, out_dir: str = "EXTRACTED"):
        for fpath in self.files:
            self.extract_file(fpath, out_dir)
        print(f"Wrote {len(self.files)} files to {out_dir}")

    def extract_file(self, fpath: str, out_dir: str):
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
            _data.write(decompressed[first_off:last_off])
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
                    _data.write(decompressed[:last_off])
                else:
                    _data.write(decompressed)
        # Now write the file out.
        _export_path, fname = op.split(fpath)
        dir_ = op.join(out_dir, _export_path)
        if dir_:
            os.makedirs(dir_, exist_ok=True)
        with open(op.join(dir_, fname), "wb") as f:
            f.write(_data.getvalue())


if __name__ == '__main__':
    # od = OodleCompressor(op.join(op.dirname(__file__), OSCONST.LIB_NAME))
    with open("NMSARC.Globals.pak", "rb") as pak:
        f = HGPakFile(pak)
        f.read()
        t1 = time.time()
        f.extract_all()
    print(f"Took {time.time() - t1}s")
