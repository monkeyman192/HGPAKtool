import fnmatch
import os
import os.path as op
import struct
from collections import namedtuple
from functools import lru_cache
from io import SEEK_CUR, SEEK_SET, BufferedReader, BufferedWriter, BytesIO
from logging import getLogger
from typing import Iterable, Literal, NamedTuple, Union

from hgpaktool.compressors import Compressor
from hgpaktool.constants import DECOMPRESSED_CHUNK_SIZE, Platform
from hgpaktool.utils import determine_bins, reqChunkBytes


class FILEINFO(NamedTuple):
    file_hash: str
    start_offset: int
    decompressed_size: int


FILEINFO_FMT = "<16s2Q"
CHUNKINFO = namedtuple("CHUNKINFO", ["size", "offset"])


logger = getLogger(__name__)


class InvalidFileException(Exception):
    pass


class PackedFile:
    """This represents a packed file within the HGPAK file"""

    __slots__ = ("offset", "size", "path", "_in_chunks", "hash_")

    def __init__(self, offset: int, size: int, path: str, hash_):
        self.offset = offset
        self.size = size
        self.path = path
        self._in_chunks = None
        self.hash_ = hash_

    @property
    def in_chunks(self) -> tuple[int, int]:
        """Determine which chunks the file is contained in."""
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
        """The offset within the first chunk that the file starts at."""
        return self.offset % DECOMPRESSED_CHUNK_SIZE

    @property
    def last_chunk_offset_end(self):
        """The offset within the last chunk where the file ends."""
        return (self.offset + self.size) % DECOMPRESSED_CHUNK_SIZE

    def __str__(self):
        return (
            f"File: {self.path}: Offset: 0x{self.offset:X}, Size: 0x{self.size:X}, "
            f"In chunks: {self.in_chunks}, Hash: {int.from_bytes(self.hash_, 'little')}"
        )

    def __repr__(self):
        return str(self)


class HGPakHeader:
    def __init__(self):
        self.version = None
        self.fileCount = 0
        self.chunk_count = 0
        self.is_compressed = False
        self.dataOffset = 0

    def read(self, fobj: BufferedReader):
        fobj.seek(0, SEEK_SET)
        if struct.unpack("5s", fobj.read(5))[0] != b"HGPAK":
            raise InvalidFileException(f"{fobj.name} does not appear to be a valid HGPAK file.")
        fobj.seek(8, SEEK_SET)
        self.version, self.fileCount, self.chunk_count, self.is_compressed, self.dataOffset = struct.unpack(
            "<QQQ?7xQ", fobj.read(0x28)
        )

    def __str__(self):
        return (
            f"HGPak Header:\n"
            f" Version {self.version}\n"
            f" Files: {self.fileCount}\n"
            f" Chunks: {self.chunk_count}\n"
            f" is Compressed: {self.is_compressed}\n"
            f" Data offset: 0x{self.dataOffset:X}\n"
        )


class HGPakFileIndex:
    def __init__(self):
        self.fileInfo: list[FILEINFO] = []

    def read(self, fileCount: int, fobj: BufferedReader, n: int = -1):
        """Read up to n entries from the file index. If n is -1 it will read all."""
        for i in range(fileCount):
            if n != -1 and i >= n:
                return
            finf = FILEINFO(*struct.unpack(FILEINFO_FMT, fobj.read(0x20)))
            self.fileInfo.append(finf)

    def write(self, fobj: BufferedWriter):
        for finf in self.fileInfo:
            fobj.write(struct.pack(FILEINFO_FMT, *finf._asdict().values()))


class HGPakChunkIndex:
    def __init__(self):
        self.chunk_sizes: tuple[int] = tuple()
        self.chunk_offset: list[int] = []

    def read(self, chunk_count: int, fobj: BufferedReader):
        self.chunk_sizes = struct.unpack(f"<{chunk_count}Q", fobj.read(8 * chunk_count))


class HGPAKFile:
    fobj: BufferedReader

    def __init__(
        self,
        filepath: str,
        platform: Union[Platform, Literal["windows", "mac", "switch"]] = Platform.WINDOWS,
    ):
        self.compressor = Compressor(platform)
        self.fpath = filepath

        self.index: list[str] = []  # The list of files contained
        self.header = HGPakHeader()
        self.fileIndex = HGPakFileIndex()
        self.chunkIndex = HGPakChunkIndex()
        self.files: dict[str, PackedFile] = {}
        self.filenames: list[str] = []

    def __enter__(self):
        # Open the provided path and build the index.
        self.fobj = open(self.fpath, "rb")
        self._parse()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        if self.fobj is not None:
            self.fobj.close()

    @property
    def _extractor_function(self):
        if self.header.is_compressed:
            return self._extract_file_compressed
        else:
            return self._extract_file_uncompressed

    def dump_index(self, dest: Union[str, os.PathLike[str]]):
        """Dump the fileindex data of the pak file to disk

        Parameters
        ----------
        dest
            The file to dump the info into
        """
        with open(dest, "w") as f:
            for filename in self.filenames:
                pf = self.files[filename]
                f.write(f"{pf}\n")

    def _parse(self):
        if self.fobj is None:
            raise Exception("HPAKFile has no initialised fobj")
        self.header.read(self.fobj)
        self.fileIndex.read(self.header.fileCount, self.fobj)
        if self.header.is_compressed is False:
            # We only need to read the filename data and then return.
            self.fobj.seek(self.header.dataOffset, SEEK_SET)
            filename_data = self.fobj.read(self.fileIndex.fileInfo[0].decompressed_size)
            self.filenames = [
                x.decode()
                for x in filename_data[: self.fileIndex.fileInfo[0].decompressed_size]
                .rstrip(b"\x0d\x0a")
                .split(b"\x0d\x0a")
            ]
            for i, fname in enumerate(self.filenames):
                if fname:
                    finf = self.fileIndex.fileInfo[i + 1]
                    self.files[fname] = PackedFile(
                        finf.start_offset, finf.decompressed_size, fname, finf.file_hash
                    )
            return

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
            self.fileIndex.fileInfo[0].decompressed_size, DECOMPRESSED_CHUNK_SIZE
        )

        # Decompress these chunks to read the filenames.
        first_chunks = b""
        for i in range(chunks_for_filenames):
            first_chunks += self._decompress_chunk(i)
        self.filenames = [
            x.decode()
            for x in first_chunks[: self.fileIndex.fileInfo[0].decompressed_size]
            .rstrip(b"\x0d\x0a")
            .split(b"\x0d\x0a")
        ]
        assert len(self.filenames) == self.header.fileCount - 1, "file count mismatch"
        for i, fname in enumerate(self.filenames):
            if fname:
                finf = self.fileIndex.fileInfo[i + 1]
                self.files[fname] = PackedFile(
                    finf.start_offset - self.header.dataOffset, finf.decompressed_size, fname, finf.file_hash
                )

    @lru_cache(maxsize=256)
    def _decompress_chunk(self, chunkIdx: int):
        self.fobj.seek(self.chunkIndex.chunk_offset[chunkIdx], SEEK_SET)
        chunk_size = self.chunkIndex.chunk_sizes[chunkIdx]
        return self.compressor.decompress(self.fobj.read(chunk_size))

    def extract(
        self,
        filters: Union[list[str], str, None] = None,
        max_bytes: int = -1,
    ) -> Iterable[tuple[str, bytes]]:
        """Extract the specified file(s) out of the pak iteratively.

        Parameters
        ----------
        filters:
            An optional list of glob patterns to pattern match against when extracting.
            Only files which match the pattern will be extracted.
            This can also just be a single string in which case just this file will be extracted.
        max_bytes:
            Maximum number of bytes to extract per-file. If this is -1 (the default) it will extract the
            entire file.

        Returns
        -------
        An iterable over the extracted files.
        This will always be an iterable even if a single filename is passed in.
        """
        if filters is not None:
            files = set()
            if isinstance(filters, str):
                if "*" in filters:
                    files.update(fnmatch.filter(self.files, filters))
                else:
                    files.add(filters.lower())
            else:
                for filter_ in filters:
                    if "*" in filter_:
                        files.update(fnmatch.filter(self.files, filter_))
                    else:
                        files.add(filter_.lower())
        else:
            files = self.files

        if len(files) == 0:
            return
        func = self._extractor_function
        for fpath in files:
            buffer = BytesIO()
            for chunk in func(fpath, max_bytes):
                buffer.write(chunk)
            if buffer.tell() > 0:
                if max_bytes != -1:
                    yield (fpath, buffer.getvalue()[:max_bytes])
                else:
                    yield (fpath, buffer.getvalue())
            else:
                continue

    def unpack(
        self,
        dest: Union[str, os.PathLike[str]],
        filters: Union[list[str], str, None] = None,
        upper: bool = False,
        max_bytes: int = -1,
    ) -> int:
        """Unpack the contained files to the specified destination

        Parameters
        ----------
        dest:
            The target folder to extract the files to.
        filters:
            An optional list of glob patterns to pattern match against when unpacking.
            Only files which match the pattern will be unpacked.
            This can also just be a single string in which case just this file will be unpacked.
        upper:
            If True, file names will be normalised to upper case.
        max_bytes:
            Maximum number of bytes to unpack per-file. If this is -1 (the default) it will extract the entire
            file.

        Returns
        -------
        Total number of files unpacked.
        """
        i = 0
        if filters is not None:
            files = set()
            if isinstance(filters, str):
                if "*" in filters:
                    files.update(fnmatch.filter(self.files, filters))
                else:
                    files.add(filters)
            else:
                for filter_ in filters:
                    if "*" in filter_:
                        files.update(fnmatch.filter(self.files, filter_))
                    else:
                        files.add(filter_)
        else:
            files = self.files
        if len(files) == 0:
            return 0

        # Loop over the files to extract their contained data.
        func = self._extractor_function
        for fpath in files:
            _export_path, fname = op.split(fpath)
            dir_ = op.join(dest, _export_path)
            if upper is True:
                dir_ = op.join(dest, _export_path.upper())
                fname = fname.upper()
            if dir_:
                os.makedirs(dir_, exist_ok=True)
            # Open the file and then loop over returned chunks and write.
            # This is more efficient, especially for compressed data than decompressing the entire file and
            # then writing it.
            with open(op.join(dir_, fname), "wb") as f:
                for chunk in func(fpath, max_bytes):
                    f.write(chunk)

            i += 1
        return i

    def _extract_file_compressed(self, fpath: str, max_bytes: int = -1) -> Iterable[bytes]:
        # Extract compressed chunks from the pak file.

        # First, get the file info.
        finf = self.files.get(fpath)
        if not finf:
            raise FileNotFoundError(f"The specified file path ({fpath!r}) doesn't exist in this pak")
        start_chunk, end_chunk = finf.in_chunks
        first_off = finf.first_chunk_offset
        last_off = finf.last_chunk_offset_end

        bytes_read = 0

        if start_chunk == end_chunk:
            # The data is contained entirely within the same chunk.
            decompressed = self._decompress_chunk(start_chunk)
            if decompressed is None:
                print(f"There was an issue decompressing chunk {start_chunk}")
                print(f"Unable to extract file: {fpath}")
                return
            if last_off:
                bytes_read += last_off - first_off
                yield decompressed[first_off:last_off]
            else:
                bytes_read += len(decompressed) - first_off
                yield decompressed[first_off:]
        else:
            for chunk_idx in range(start_chunk, end_chunk + 1):
                decompressed = self._decompress_chunk(chunk_idx)
                if decompressed is None:
                    print(f"There was an issue decompressing chunk {chunk_idx}")
                    print(f"Unable to extract file: {fpath}")
                    return
                if chunk_idx == start_chunk:
                    bytes_read += len(decompressed) - first_off
                    yield decompressed[first_off:]
                elif chunk_idx == end_chunk:
                    if not last_off:
                        bytes_read += len(decompressed)
                        yield decompressed
                    else:
                        bytes_read += last_off
                        yield decompressed[:last_off]
                else:
                    bytes_read += len(decompressed)
                    yield decompressed
                if max_bytes != -1:
                    # Once we have at least as many bytes as max_bytes, then stop.
                    if bytes_read >= max_bytes:
                        break

    def _extract_file_uncompressed(self, fpath: str, max_bytes: int = -1) -> Iterable[bytes]:
        # Extract uncompressed chunks from the pak file.
        finf = self.files.get(fpath)
        if not finf:
            raise FileNotFoundError(f"The specified file path ({fpath!r}) doesn't exist in this pak")
        self.fobj.seek(finf.offset)

        extract_size = finf.size
        if max_bytes != -1:
            extract_size = max_bytes

        # Read each of the chunks
        chunks = extract_size // DECOMPRESSED_CHUNK_SIZE
        if not chunks:
            yield self.fobj.read(extract_size)
        else:
            # First, read all the full chunk data.
            for _ in range(chunks):
                yield self.fobj.read(DECOMPRESSED_CHUNK_SIZE)
            # Then finally, read the remainder is there is any.
            if (rem := extract_size % DECOMPRESSED_CHUNK_SIZE) != 0:
                yield self.fobj.read(rem)

    def pack(self):
        # Implement later...
        raise NotImplementedError("Re-packing support currently not enabled")


if __name__ == "__main__":
    import time

    filename_mapping: dict[str, HGPAKFile] = {}
    namehash_guid_mapping: dict[int, int] = {}

    limit = 0

    t1 = time.perf_counter()
    loaded_paks = 0
    pak_dir = r"C:\Program Files (x86)\Steam\steamapps\common\No Man's Sky\GAMEDATA\PCBANKS"
    for fname in os.listdir(pak_dir):
        if fname.endswith(".pak"):
            print(f"Parsing {fname}")
            with HGPAKFile(op.join(pak_dir, fname)) as pak:
                for _fname in pak.filenames:
                    filename_mapping[_fname] = pak
                    if _fname.lower().endswith(".mbin"):
                        for fname, _data in pak.extract(_fname, max_bytes=0x20):
                            namehash, guid = struct.unpack("<12xIQ8x", _data)
                            namehash_guid_mapping[namehash] = guid
                            if namehash == 0x2E:
                                print(_fname)

                loaded_paks += 1
    print(
        f"Parsed {loaded_paks} pak files with {len(filename_mapping)} files in "
        f"{time.perf_counter() - t1:.6f}s"
    )
    # for namehash, guid in namehash_guid_mapping.items():
    #     print(hex(namehash), hex(guid))
