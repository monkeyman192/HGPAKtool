import argparse
import importlib.util
import json
import os
import os.path as op
import platform
import sys
import time
from typing import Literal, Optional

import hgpaktool.constants
from hgpaktool import __version__
from hgpaktool.api import HGPAKFile
from hgpaktool.os_funcs import Platform, platform_map
from hgpaktool.utils import make_filename_unixhidden, should_unpack

# Try import both lz4 and zstd.
# zstd is only required for windows, and lz4 is only required for mac, so if either fails, don't break
# immediately, only break once we know what platform we are targeting.

zstd_imported = False
try:
    if importlib.util.find_spec("zstandard") is not None:
        zstd_imported = True
except ModuleNotFoundError:
    pass

lz4_imported = False
try:
    if importlib.util.find_spec("lz4", "block") is not None:
        lz4_imported = True
except ModuleNotFoundError:
    pass


class HGPAKNamespace(argparse.Namespace):
    plain: bool
    contents: bool
    verbose: bool
    platform: Literal["windows", "mac", "switch"]
    compress: bool
    output: str
    filter: list[str]
    json: str
    dryrun: bool
    upper: bool
    unpack: bool
    pack: bool
    repack: bool
    filenames: list[str]
    list: bool


def run():
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
        "-p",
        "--plain",
        action="store_true",
        default=False,
        help=("Whether to output any generation informational files in a simplified format"),
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
        "-Z", "--compress", action="store_true", help="Whether or not to compress the provided files."
    )
    parser.add_argument(
        "-O",
        "--output",
        required=False,
        help=(
            "The directory to place extracted files in. If not provided, falls back to a folder called "
            "'EXTRACTED' in the current directory."
        ),
    )
    parser.add_argument(
        "-f",
        "--filter",
        action="append",
        help="A glob pattern which can be used to filter out the files which are to be extracted.",
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
        ),
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
        help="If provided, extracted filenames will be converted to UPPERCASE.",
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
        help="Repack the files for a given vanilla pak name.",
    )
    parser.add_argument(
        "filenames",
        nargs="+",
        help=(
            "The file(s) to pack or unpack. If this is a list of pak files or a directory, then it will be "
            "assumed that the files need to be unpacked.\nIf the filename is a single json file in the same "
            "format as produced by the -L flag, then it will also be unpacked as per the listed pak files "
            "and listed contents."
        ),
    )

    args = HGPAKNamespace()
    args = parser.parse_args(namespace=args)
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

    if args.platform == Platform.WINDOWS:
        if zstd_imported is False:
            print("You need to install zstandard for this code to work. Please run `pip install zstandard`")
            sys.exit(1)
    elif args.platform == Platform.MAC:
        if lz4_imported is False:
            print("You need to install lz4 for this code to work. Please run `pip install lz4`")
            sys.exit(1)
    elif args.platform == Platform.SWITCH:
        # Decompressed chunk size on switch is 128kb
        hgpaktool.constants.DECOMPRESSED_CHUNK_SIZE = 0x20000
        hgpaktool.constants.CLEAN_BYTES = b"\x00" * hgpaktool.constants.DECOMPRESSED_CHUNK_SIZE  # noqa

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
                        print(
                            f"Cannot extract {pak_path} as it's a relative path. "
                            "Either provide the absolute path, or provide the root directory as the "
                            "'filename' argument."
                        )
                        continue
                if not abs_pak_path.lower().endswith(".pak"):
                    print(f"Skipping {pak_path}: Not a valid file to extract.")
                    continue
                with HGPAKFile(abs_pak_path, args.platform) as pak:
                    print(f"Reading {op.basename(abs_pak_path)}")
                    file_count += pak.unpack(output, req_contents, args.upper)
                pack_count += 1
        else:
            for filename in filenames:
                if op.isdir(filename):
                    for fname in os.listdir(filename):
                        if not fname.lower().endswith(".pak"):
                            print(f"{fname} is not a valid path to extract.")
                            continue
                        print(f"Reading {fname}")
                        with HGPAKFile(op.join(filename, fname), args.platform) as pak:
                            # Generate a list of the contained files
                            fullpath = op.join(op.realpath(filename), fname)
                            filename_data[fullpath] = pak.filenames
                            if not args.list:
                                file_count += pak.unpack(output, args.filter)
                        pack_count += 1
                else:
                    with HGPAKFile(filename, args.platform) as pak:
                        # Generate a list of the contained files
                        filename_data[op.realpath(filename)] = pak.filenames
                        if not args.list:
                            file_count += pak.unpack(output, args.filter)
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
                    f.write(json.dumps({"filenames": filenames, "root_dir": output}))
        else:
            print(f"Unpacked {file_count} files from {pack_count} .pak's in {time.time() - t1:3f}s")
