from collections import defaultdict
from enum import Enum
from typing import Literal

# The game decompresses chunks to this size blocks (64kb)
DECOMPRESSED_CHUNK_SIZE = 0x10000
CLEAN_BYTES = b"\x00" * DECOMPRESSED_CHUNK_SIZE


class Platform(str, Enum):
    WINDOWS = "windows"
    MAC = "mac"
    LINUX = "linux"
    SWITCH = "switch"


PlatformLiteral = Literal["windows", "mac", "switch", "linux"]


platform_map = defaultdict(
    lambda: "windows",
    {
        "Windows": Platform.WINDOWS.value,
        "Linux": Platform.LINUX.value,
        "Darwin": Platform.MAC.value,
    },
)


class Compression(str, Enum):
    ZSTD = "zstd"
    LZ4 = "lz4"
    OODLE = "oodle"


CompressionLiteral = Literal["zstd", "lz4", "oodle"]


compression_map = defaultdict(
    lambda: Compression.ZSTD,
    {
        "windows": Compression.ZSTD,
        "linux": Compression.ZSTD,
        "mac": Compression.LZ4,
        "switch": Compression.OODLE,
    },
)
