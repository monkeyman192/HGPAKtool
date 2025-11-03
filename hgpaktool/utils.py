import os.path as op


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


def make_filename_unixhidden(path: str) -> str:
    """Add a dot at the beginning of a filename, respecting its path

    Parameters
    ----------
    path:
        The absolute or relative file path.

    Returns
    -------
    The hidden filename version.
    """

    return op.join(op.dirname(path), "." + op.basename(path))


def should_unpack(filenames: list[str]) -> bool:
    """Determine whether we should unpack or not.
    This will return true if every file in the list has the extension .pak or we get a folder.
    """
    return (
        all([x.lower().endswith(".pak") for x in filenames])
        or (len(filenames) == 1 and op.isdir(filenames[0]))
        or (len(filenames) == 1 and filenames[0].lower().endswith(".json"))
    )
