import hashlib
import os
import os.path as op
import pathlib
from typing import Union


def determine_bins(num_bytes: int, bin_size: int = 0x10) -> int:
    """Determine the number of bins required to hold the requested number of bytes."""
    return (num_bytes + bin_size - 1) // bin_size


def reqChunkBytes(chunk_size: int) -> int:
    """Get the minimum required number of bytes which is a multiple of 0x10
    which can fit the specified number of bytes in the chunk."""
    return 0x10 * determine_bins(chunk_size)


def roundup(x: int) -> int:
    """Round up a number to the nearest 0x10 byte boundary."""
    # x >> 4 << 4 will round down to the nearest 0x10.
    # Then check if the number was higher by bitwise and-ing 0xF.
    # If it is, then add the 0x10.
    return (x >> 4 << 4) + ((x & 0xF) and 0x10)


def padding(x: int) -> int:
    """Determine the number of bytes required to pad the value to a 0x10
    byte boundary"""
    return (0x10 - (x & 0xF)) & 0xF


def should_unpack(filenames: list[str]) -> bool:
    """Determine whether we should unpack or not.
    This will return true if every file in the list has the extension .pak or we get a folder.
    """
    return (
        all([x.lower().endswith(".pak") for x in filenames])
        or (len(filenames) == 1 and op.isdir(filenames[0]))
        or (len(filenames) == 1 and filenames[0].lower().endswith(".json"))
    )


def hash_path(path: str) -> bytes:
    # This is the hash of the filename as per how the game generates them.
    return hashlib.md5(normalise_path(path).encode()).digest()


def normalise_path(path: str) -> str:
    return pathlib.PureWindowsPath(path).as_posix().lower()


def parse_manifest(manifest: Union[str, os.PathLike[str]]) -> list[str]:
    """Parse the manifest file and extract the list of files contained."""
    fnames = []
    with open(manifest, "r") as f:
        for line in f:
            sline = line.strip()
            if not sline:
                continue
            fnames.append(normalise_path(sline))
    return fnames
