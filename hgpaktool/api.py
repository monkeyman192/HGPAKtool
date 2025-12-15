import fnmatch
import hashlib
import os
import os.path as op
import struct
from collections import namedtuple
from dataclasses import dataclass
from functools import lru_cache
from io import SEEK_CUR, SEEK_SET, BufferedReader, BufferedWriter, BytesIO
from logging import NullHandler, getLogger
from typing import Iterable, Mapping, Optional, Union

from hgpaktool.buffers import FixedBuffer, chunked_file_reader
from hgpaktool.compressors import Compressor
from hgpaktool.constants import (
    DECOMPRESSED_CHUNK_SIZE,
    Compression,
    Platform,
    PlatformLiteral,
    compression_map,
)
from hgpaktool.utils import (
    determine_bins,
    hash_path,
    normalise_path,
    padding,
    parse_manifest,
    reqChunkBytes,
    roundup,
)


@dataclass
class FileInfo:
    file_hash_: bytes
    start_offset: int
    decompressed_size: int

    @property
    def file_hash(self) -> int:
        return int.from_bytes(self.file_hash_, "big", signed=False)

    def values(self):
        return (self.file_hash_, self.start_offset, self.decompressed_size)

    def __str__(self):
        return (
            f"Hash: 0x{self.file_hash:X}, offset: 0x{self.start_offset:X}, "
            f"decompressed size: 0x{self.decompressed_size:X}"
        )


FILEINFO_FMT = "<16s2Q"
CHUNKINFO = namedtuple("CHUNKINFO", ["size", "offset"])

HGPAKFMT_VERSION = 2


logger = getLogger(__name__)
logger.addHandler(NullHandler())


class InvalidFileException(Exception):
    pass


class PackedFile:
    """This represents a packed file within the HGPAK file"""

    __slots__ = ("offset", "size", "path", "_in_chunks")

    def __init__(self, offset: int, size: int, path: str):
        self.offset = offset
        self.size = size
        self.path = path
        self._in_chunks = None

    @property
    def filename_hash(self):
        # This hash is the md5 hash of the name.
        return hash_path(self.path)

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
            f"In chunks: {self.in_chunks}"
        )

    def __repr__(self):
        return str(self)


class HGPakHeader:
    def __init__(self):
        self.version = HGPAKFMT_VERSION
        self.file_count = 0
        self.chunk_count = 0
        self.is_compressed = False
        self.data_offset = 0

    def read(self, fobj: BufferedReader):
        fobj.seek(0, SEEK_SET)
        if struct.unpack("5s", fobj.read(5))[0] != b"HGPAK":
            raise InvalidFileException(f"{fobj.name} does not appear to be a valid HGPAK file.")
        fobj.seek(8, SEEK_SET)
        self.version, self.file_count, self.chunk_count, self.is_compressed, self.data_offset = struct.unpack(
            "<QQQ?7xQ", fobj.read(0x28)
        )
        if self.version != HGPAKFMT_VERSION:
            raise InvalidFileException(f"{fobj.name} does not appear to be a valid HGPAK file.")

    def write(self, fobj: BufferedWriter):
        # First, write the magic
        fobj.write(struct.pack("<5s3x", b"HGPAK"))
        fobj.write(
            struct.pack(
                "<QQQ?7xQ",
                self.version,
                self.file_count,
                self.chunk_count,
                self.is_compressed,
                self.data_offset,
            )
        )

    def __str__(self):
        return (
            f"HGPak Header:\n"
            f" Version {self.version}\n"
            f" Files: {self.file_count}\n"
            f" Chunks: {self.chunk_count}\n"
            f" is Compressed: {self.is_compressed}\n"
            f" Data offset: 0x{self.data_offset:X}\n"
        )


class HGPakFileIndex:
    def __init__(self):
        self.fileInfo: list[FileInfo] = []

    def read(self, file_count: int, fobj: BufferedReader, n: int = -1):
        """Read up to n entries from the file index. If n is -1 it will read all."""
        for i in range(file_count):
            if n != -1 and i >= n:
                return
            finf = FileInfo(*struct.unpack(FILEINFO_FMT, fobj.read(0x20)))
            self.fileInfo.append(finf)

    def write(self, fobj: BufferedWriter):
        for finf in self.fileInfo:
            fobj.write(struct.pack(FILEINFO_FMT, *finf.values()))

    def __str__(self):
        res = "File Index\n----------\n"
        res += "\n".join([str(finf) for finf in self.fileInfo])
        return res


class HGPakChunkIndex:
    chunk_sizes: tuple[int]

    def __init__(self):
        self.chunk_offset: list[int] = []

    def read(self, chunk_count: int, fobj: BufferedReader):
        self.chunk_sizes = struct.unpack(f"<{chunk_count}Q", fobj.read(8 * chunk_count))


class HGPAKFile:
    fobj: BufferedReader

    def __init__(
        self,
        filepath: Union[str, os.PathLike[str]],
        platform: Union[Platform, PlatformLiteral] = Platform.WINDOWS,
    ):
        self.compressor = Compressor(compression_map.get(platform, Compression.ZSTD))
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
    def pak_name(self) -> str:
        return op.basename(self.fpath)

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
        self.fileIndex.read(self.header.file_count, self.fobj)
        if self.header.is_compressed is False:
            # We only need to read the filename data and then return.
            self.fobj.seek(self.header.data_offset, SEEK_SET)
            filename_data = self.fobj.read(self.fileIndex.fileInfo[0].decompressed_size)
            self.filenames = [
                x.decode()
                for x in filename_data[: self.fileIndex.fileInfo[0].decompressed_size]
                .rstrip(b"\r\n")
                .split(b"\r\n")
            ]
            for i, fname in enumerate(self.filenames):
                if fname:
                    finf = self.fileIndex.fileInfo[i + 1]
                    self.files[fname] = PackedFile(finf.start_offset, finf.decompressed_size, fname)
            return

        if self.header.is_compressed:
            self.chunkIndex.read(self.header.chunk_count, self.fobj)
        # Finally, we should now be at the start of the compressed data.
        # Instead of reading it all into a buffer. We'll just jump over to
        # get the offsets for easier reading later.
        self.fobj.seek(self.header.data_offset, SEEK_SET)
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
            if (_chunk := self._decompress_chunk(i)) is not None:
                first_chunks += _chunk
            else:
                logger.error(f"There was an error reading the filename section for {self.fpath}")
        self.filenames = [
            x.decode()
            for x in first_chunks[: self.fileIndex.fileInfo[0].decompressed_size]
            .rstrip(b"\r\n")
            .split(b"\r\n")
        ]
        assert len(self.filenames) == self.header.file_count - 1, "file count mismatch"
        for i, fname in enumerate(self.filenames):
            if fname:
                finf = self.fileIndex.fileInfo[i + 1]
                self.files[fname] = PackedFile(
                    finf.start_offset - self.header.data_offset, finf.decompressed_size, fname
                )

    @lru_cache(maxsize=256)
    def _decompress_chunk(self, chunkIdx: int) -> Optional[bytes]:
        self.fobj.seek(self.chunkIndex.chunk_offset[chunkIdx], SEEK_SET)
        chunk_size = self.chunkIndex.chunk_sizes[chunkIdx]
        return self.compressor.decompress(self.fobj.read(chunk_size))

    def _get_filtered_filelist(
        self, filters: Union[list[str], str, None] = None
    ) -> Mapping[str, Optional[PackedFile]]:
        """Filter the known file list.
        This returns a dictionary instead of a set so that the order is always the same.
        """
        files = {}
        if filters is not None:
            if isinstance(filters, str):
                if "*" in filters:
                    for filtered in fnmatch.filter(self.files, filters.lower()):
                        files[filtered] = None
                else:
                    files[filters.lower()] = None
            else:
                for filter_ in filters:
                    if "*" in filter_:
                        for filtered in fnmatch.filter(self.files, filter_.lower()):
                            files[filtered] = None
                    else:
                        files[filter_.lower()] = None
        else:
            files = self.files
        return files

    def get_hashes(
        self,
        filters: Union[list[str], str, None] = None,
        mask_guid: bool = False,
        algorithm: str = "md5",
    ) -> Iterable[tuple[str, str]]:
        """Generate hashes for the specified file(s) in the pak.

        Parameters
        ----------
        filters:
            An optional list of glob patterns to pattern match against when extracting.
            Only files which match the pattern will be extracted.
            This can also just be a single string in which case just this file will be extracted.
        mask_guid:
            If True, the GUID in the mbin file will be masked.
            This will only happen if the file is an mbin file (checked base on the magic), otherwise it will
            do nothing.
        algorithm:
            The name of the algorithm. This must be one provided by python (cf. hashlib.algorithms_available).

        Returns
        -------
        An iterable over the hashes.
        This will always be an iterable even if a single filename is passed in.
        """
        files = self._get_filtered_filelist(filters)

        if len(files) == 0:
            return

        func = self._extractor_function
        _base_hash = hashlib.new(algorithm)
        for fpath in files:
            _hash = _base_hash.copy()
            for i, chunk in enumerate(func(fpath)):
                if len(chunk) == 0:
                    continue
                if i == 0:
                    if mask_guid:
                        magic = chunk[:8]
                        if (
                            magic == b"\xcc\xcc\xcc\xcc\xcc\xcc\xcc\xcc"
                            or magic == b"\xdd\xdd\xdd\xdd\xdd\xdd\xdd\xdd"
                        ):
                            chunk = chunk[:0x10] + b"\x00" * 8 + chunk[0x18:]
                _hash.update(chunk)
            yield (fpath, _hash.hexdigest().upper())

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
        files = self._get_filtered_filelist(filters)

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
        write_manifest: bool = False,
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
            Note: This setting will be ignored if ``write_manifest`` is True.
        max_bytes:
            Maximum number of bytes to unpack per-file. If this is -1 (the default) it will extract the entire
            file.
        write_manifest:
            Whether or not to write the manifest for the pak to disk.
            This is required if you want to repack the archive.
            This file will be written at the top level of the extraction directory.

        Returns
        -------
        Total number of files unpacked.
        """
        files = self._get_filtered_filelist(filters)

        if len(files) == 0:
            return 0

        if upper and write_manifest:
            logger.warning(
                "`upper` and `write_manifest` arguments are both set to True. This combination is not valid."
                " The value for `upper` will be ignored."
            )

        # Loop over the files to extract their contained data.
        i = 0
        func = self._extractor_function
        for fpath in files:
            _export_path, fname = op.split(fpath)
            dir_ = op.join(dest, _export_path)
            # If we are writing the manifest, ignore the upper flag.
            if upper and not write_manifest:
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

        if write_manifest:
            with open(op.join(dest, f"{self.pak_name}.manifest"), "w") as f:
                for fname in files:
                    f.write(fname + "\r\n")

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
                logger.error(f"There was an issue decompressing chunk {start_chunk}")
                logger.error(f"Unable to extract file: {fpath}")
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
                    logger.error(f"There was an issue decompressing chunk {chunk_idx}")
                    logger.error(f"Unable to extract file: {fpath}")
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

    def _pack_files(
        self,
        filepaths: list[str],
        root_dir: Union[str, os.PathLike[str]],
        pak_fpath: Union[str, os.PathLike[str]],
        compress: bool = True,
    ):
        """Pack the provided files.
        The first file MUST be a .manifest file containing a list of the files in the .pak file.
        Note that since this is an internal method this will not be validated."""
        fullpaths: list[str] = []

        # Loop over the filepaths and get their name hashes and sizes.
        for fpath in filepaths:
            realpath = op.realpath(op.join(root_dir, fpath))
            fullpaths.append(realpath)
            self.fileIndex.fileInfo.append(FileInfo(hash_path(fpath), 0, os.stat(realpath).st_size))
        file_count = len(self.fileIndex.fileInfo)

        # Determine the offsets of each file in uncompressed space.
        curr_total_data_size = 0
        for finfo in self.fileIndex.fileInfo:
            finfo.start_offset = curr_total_data_size
            curr_total_data_size += roundup(finfo.decompressed_size)

        # Now that we have the total uncompressed data, we may determine the total number of chunks.
        chunk_count = determine_bins(curr_total_data_size, DECOMPRESSED_CHUNK_SIZE)

        # Now that we know the total chunk count and file count, we know how big the "pre-data" data is.
        data_offset = 0x30 + 0x20 * file_count + compress * 0x8 * chunk_count
        # Also add padding so that the data always starts at an offset which is a
        # multiple of 0x10
        extra_padding = padding(data_offset)
        data_offset += extra_padding

        # Fixup the offsets now that we know the data offset.
        for finfo in self.fileIndex.fileInfo:
            finfo.start_offset += data_offset

        # Write the data back to the file.
        self.header.file_count = file_count
        self.header.chunk_count = chunk_count
        self.header.is_compressed = compress
        self.header.data_offset = data_offset

        with open(pak_fpath, "wb") as f:
            self.header.write(f)
            self.fileIndex.write(f)
            # Reserve space for the compressed chunk sizes if the file is compressed.
            chunk_index_offset = f.tell()
            if compress:
                f.write(b"\x00" * (8 * chunk_count + extra_padding))

            sub_buffer = FixedBuffer(f, self.compressor, compress)

            # Write all the files into the pak.
            for _data in chunked_file_reader(fullpaths):
                sub_buffer.add_bytes(_data)
            # Finally, call write_to_main_buffer to flush the last block to the file.
            sub_buffer.write_to_main_buffer()

            if compress:
                f.seek(chunk_index_offset, SEEK_SET)
                # Get the compressed block sizes and write to the chunk info section.
                for chunk_size in sub_buffer.compressed_block_sizes:
                    f.write(struct.pack("<Q", chunk_size))

    @classmethod
    def repack(
        cls,
        manifest: Union[str, os.PathLike[str]],
        out_fpath: Optional[Union[str, os.PathLike[str]]] = None,
        compress: bool = True,
        platform: Union[Platform, PlatformLiteral] = Platform.WINDOWS,
    ):
        """Repack the provided manifest file

        Parameters
        ----------
        manifest:
            The path to the manifest file to be repacked.
            This file should be the original file which was unpacked by HGPAKTool.
            If this file is modified it MUST use CRLF line endings and have a trailing line ending.
        out_fpath:
            The destination pak path. If not provided this will fallback to the name of the pak based on the
            manifest and will be written to the same directory as the manifest.
        compress:
            Whether or not to compress the data contained within the pak file.
            Compression format will depend on what platform is provided.
        platform:
            The target platform for the pak file.
        """
        # Generate the required paths based on the manifest file. It's basically our "source of truth"
        real_manifest_path = op.realpath(manifest)
        manifest_dir = op.dirname(real_manifest_path)
        manifest_name = op.basename(manifest)
        pak_name, _ = op.splitext(manifest_name)
        pak = cls(pak_name, platform=platform)
        # Parse the manifest so that we have a file list:
        file_list = parse_manifest(manifest)
        if out_fpath is None:
            out_fpath = op.join(manifest_dir, pak_name)
        pak._pack_files(
            [normalise_path(op.basename(manifest)), *file_list],
            manifest_dir,
            out_fpath,
            compress,
        )
