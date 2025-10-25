from collections import defaultdict
from enum import Enum

# The game decompresses chunks to this size blocks (64kb)
DECOMPRESSED_CHUNK_SIZE = 0x10000
CLEAN_BYTES = b"\x00" * DECOMPRESSED_CHUNK_SIZE


class Platform(str, Enum):
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
