import argparse
import importlib.util
import json
import logging
import os
import os.path as op
import pathlib
import platform
import re
import sys
import time
from typing import Literal

import hgpaktool.constants
from hgpaktool import __version__
from hgpaktool.api import HGPAKFile, InvalidFileException
from hgpaktool.constants import Platform, platform_map
from hgpaktool.utils import make_filename_unixhidden, should_unpack

# Try import both lz4 and zstd.
# zstd is only required for windows, and lz4 is only required for mac, so if either fails, don't break
# immediately, only break once we know what platform we are targeting.

logger = logging.getLogger("hgpaktool")
logger.addHandler(logging.StreamHandler())

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

VERSION_RE = re.compile(
    r"""
    (?P<version>
        (?P<major_ver>\d+)
        \.
        (?P<minor_ver>\d+)
        \.
        (?P<patch_ver>\d+)
        \.dev(?P<revision>\d+)
    )
    |
    (^\d+\.\d+\.\d+$)
    """,
    re.VERBOSE,
)

# IMPORTANT: This value MUST be reset to 0 when a new version is tagged.
CLI_REVISION = 1
CLI_VERSION = __version__
if (m := re.match(VERSION_RE, __version__)) is not None:
    md = m.groupdict()
    if md["version"] is not None:
        # We are some way away from the original version. Decrement patch and use above CLI_REVISION value.
        prev_patch_ver = max(int(md["patch_ver"]) - 1, 0)
        CLI_VERSION = f"{md['major_ver']}.{md['minor_ver']}.{prev_patch_ver}.cli{CLI_REVISION}"


class SmartFormatter(argparse.HelpFormatter):
    # "Smaerter" help formatter c/o https://stackoverflow.com/a/22157136
    def _split_lines(self, text, width):
        if text.startswith("R|"):
            return text[2:].splitlines()
        # this is the RawTextHelpFormatter._split_lines
        return argparse.HelpFormatter._split_lines(self, text, width)


class HGPAKNamespace(argparse.Namespace):
    plain: bool
    contents: bool
    platform: Literal["windows", "mac", "switch", "linux"]
    compress: bool
    output: os.PathLike[str]
    filter: list[str]
    json: os.PathLike[str]
    dryrun: bool
    upper: bool
    unpack: bool
    pack: bool
    repack: bool
    filenames: list[str]
    list: bool
    hash: os.PathLike[str]
    no_hash_guid: bool
    verbose: int


def update_hashes(
    pak: HGPAKFile,
    hash_data: dict,
    pakname: str,
    args: HGPAKNamespace,
):
    pak_hash_data = {}
    for ifname, _hash in pak.get_hashes(args.filter, args.no_hash_guid):
        if args.upper:
            pak_hash_data[ifname.upper()] = _hash
        else:
            pak_hash_data[ifname] = _hash
    if pak_hash_data:
        if args.plain:
            hash_data.update(pak_hash_data)
        else:
            hash_data[op.basename(pakname)] = pak_hash_data


def run():
    parser = argparse.ArgumentParser(
        prog=f"HGPAKtool ({CLI_VERSION})",
        description="A tool for handling HG's custom .pak format",
        formatter_class=SmartFormatter,
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
        "--platform",
        choices=("windows", "mac", "linux", "switch"),
        default=platform_map[platform.system()],
        const=platform_map[platform.system()],
        nargs="?",
        help=(
            "R|The platform to unpack the files for. Default: %(default)s.\n"
            "Note: This changes the compression algorithm used to compress or decompress the files like so:\n"
            " - windows -> ZSTD\n"
            " - linux   -> ZSTD\n"
            " - macos   -> LZ4\n"
            " - switch  -> Oodle\n"
            "This will default to the platform you are on, so if you wish to decompress a file from a "
            "different platform or with a different compression algorithm, choose from the above list."
        ),
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
        type=pathlib.Path,
    )
    parser.add_argument(
        "-f",
        "--filter",
        action="append",
        help=(
            "R|A glob pattern which can be used to filter out the files which are to be extracted.\n"
            "This argument can be provided multiple times and the filters will be individually be applied to "
            "full set of files in each pak (ie. filters are OR'd, not AND'd)."
        ),
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
        type=pathlib.Path,
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
    parser.add_argument(
        "--hash",
        help="The name of a file to dump the hash details of the contained files",
        type=pathlib.Path,
        metavar="OUTPUT.JSON",
    )
    parser.add_argument(
        "--no_hash_guid",
        action="store_true",
        default=False,
        help="Whether or not to hash the GUID for mbin files",
    )
    parser.add_argument("-v", "--verbose", action="count", default=0, help="Increase logging verbosity")
    pup_group = parser.add_mutually_exclusive_group()  # pup = pack/unpack
    pup_group.add_argument(
        "-U",
        "--unpack",
        action="store_true",
        help="Unpack the files from the provided pak files.",
    )
    pup_group.add_argument(
        "-P",
        "--pack",
        action="store_true",
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

    generate_hashes = args.hash is not None
    verbosity = args.verbose
    if verbosity == 1:
        logger.setLevel(logging.INFO)
    elif verbosity >= 2:
        logger.setLevel(logging.DEBUG)

    if args.pack:
        mode = "pack"
    elif args.unpack:
        mode = "unpack"
    else:
        if generate_hashes:
            mode = "hash"
        else:
            if should_unpack(filenames):
                # All the files provided are pak files, so decompress them unless we
                # have been asked to repack them
                if args.repack:
                    mode = "repack"
                else:
                    mode = "unpack"
            else:
                mode = "pack"

    if args.platform == Platform.WINDOWS or args.platform == Platform.LINUX:
        if zstd_imported is False:
            logger.error(
                "You need to install zstandard for this code to work. Please run `pip install zstandard`"
            )
            sys.exit(1)
    elif args.platform == Platform.MAC:
        if lz4_imported is False:
            logger.error("You need to install lz4 for this code to work. Please run `pip install lz4`")
            sys.exit(1)
    elif args.platform == Platform.SWITCH:
        # Decompressed chunk size on switch is 128kb
        hgpaktool.constants.DECOMPRESSED_CHUNK_SIZE = 0x20000
        hgpaktool.constants.CLEAN_BYTES = b"\x00" * hgpaktool.constants.DECOMPRESSED_CHUNK_SIZE  # noqa

    t1 = time.perf_counter()

    if mode == "unpack":
        output = op.abspath(args.output or "EXTRACTED")
        if not op.exists(output) and not args.list:
            os.makedirs(output, exist_ok=True)
        pack_count = 0
        file_count = 0
        filename_data: dict[str, list[str]] = {}

        json_file = None
        if args.json:
            json_file = args.json
        elif len(filenames) == 1 and filenames[0].lower().endswith(".json"):
            json_file = filenames[0]

        if generate_hashes:
            hash_data = {}

        if json_file is not None:
            root_dir = None
            if len(filenames) > 1:
                logger.error(
                    "Cannot unpack with json from multiple directories. Please only provide one directory"
                )
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
                        logger.error(
                            f"Cannot extract {pak_path} as it's a relative path. "
                            "Either provide the absolute path, or provide the root directory as the "
                            "'filename' argument."
                        )
                        continue
                if not abs_pak_path.lower().endswith(".pak"):
                    logger.debug(f"Skipping {pak_path}: Not a valid file to extract.")
                    continue
                try:
                    logger.debug(f"Reading {op.basename(abs_pak_path)}")
                    with HGPAKFile(abs_pak_path, args.platform) as pak:
                        file_count += pak.unpack(output, req_contents, args.upper)
                        if generate_hashes:
                            update_hashes(pak, hash_data, abs_pak_path, args)
                        pack_count += 1
                except InvalidFileException:
                    logger.debug(f"{abs_pak_path} is not a valid .pak file. Skipping")
        else:
            for filename in filenames:
                if op.isdir(filename):
                    for fname in os.listdir(filename):
                        if not fname.lower().endswith(".pak"):
                            logger.debug(f"{fname} is not a valid path to extract.")
                            continue
                        try:
                            logger.debug(f"Reading {fname}")
                            with HGPAKFile(op.join(filename, fname), args.platform) as pak:
                                if not args.list:
                                    file_count += pak.unpack(output, args.filter, args.upper)
                                else:
                                    # Generate a list of the contained files
                                    fullpath = op.join(op.realpath(filename), fname)
                                    fnames = list(pak._get_filtered_filelist(args.filter).keys())
                                    if fnames:
                                        filename_data[fullpath] = fnames
                                if generate_hashes:
                                    update_hashes(pak, hash_data, fname, args)
                                pack_count += 1
                        except InvalidFileException:
                            logger.debug(f"{op.join(filename, fname)} is not a valid .pak file. Skipping")
                else:
                    try:
                        logger.debug(f"Reading {filename}")
                        with HGPAKFile(filename, args.platform) as pak:
                            if not args.list:
                                file_count += pak.unpack(output, args.filter, args.upper)
                            else:
                                # Generate a list of the contained files
                                fnames = list(pak._get_filtered_filelist(args.filter).keys())
                                if fnames:
                                    filename_data[op.realpath(filename)] = fnames
                            if generate_hashes:
                                update_hashes(pak, hash_data, filename, args)
                            pack_count += 1
                    except InvalidFileException:
                        logger.debug(f"{filename} is not a valid .pak file. Skipping")

        if args.list:
            if args.plain:
                with open("filenames.txt", "w") as f:
                    for pakname, filenames in filename_data.items():
                        f.write(f"Listing {pakname}\n")
                        for fname in filenames:
                            if args.upper:
                                f.write(fname.upper() + "\n")
                            else:
                                f.write(fname + "\n")
            else:
                with open("filenames.json", "w") as f:
                    if args.upper:
                        # Recase all the internal filenames
                        data = {}
                        for pakname, filenames in filename_data.items():
                            data[pakname] = [x.upper() for x in filenames]
                        f.write(json.dumps(data, indent=2))
                    else:
                        f.write(json.dumps(filename_data, indent=2))
            logger.info(f"Listed contents of {pack_count} .pak's in {time.perf_counter() - t1:.3f}s")
        elif args.contents:
            for pakname, filenames in filename_data.items():
                with open(f"{make_filename_unixhidden(pakname)}.contents", "w") as f:
                    f.write(json.dumps({"filenames": filenames, "root_dir": output}))
        else:
            logger.info(
                f"Unpacked {file_count} files from {pack_count} .pak's in {time.perf_counter() - t1:.3f}s"
            )
    elif mode == "hash":
        hash_data = {}
        pak_count = 0
        for filename in filenames:
            if op.isdir(filename):
                for fname in os.listdir(filename):
                    if not fname.lower().endswith(".pak"):
                        continue
                    try:
                        logger.debug(f"Reading {fname}")
                        with HGPAKFile(op.join(filename, fname), args.platform) as pak:
                            update_hashes(pak, hash_data, fname, args)
                            pak_count += 1
                    except InvalidFileException:
                        logger.debug(f"{op.join(filename, fname)} is not a valid .pak file. Skipping")
            else:
                try:
                    logger.debug(f"Reading {filename}")
                    with HGPAKFile(filename, args.platform) as pak:
                        update_hashes(pak, hash_data, filename, args)
                        pak_count += 1
                except InvalidFileException:
                    logger.debug(f"{op.join(filename, fname)} is not a valid .pak file. Skipping")
        if hash_data:
            with open(args.hash, "w") as f:
                json.dump(hash_data, f, indent=1)
        logger.info(f"Hashed contents of {pak_count} .pak's in {time.perf_counter() - t1:.3f}s")
    elif mode == "pack" or mode == "repack":
        logger.error(
            "Packing and repacking currently not supported. This will return in the future. For now use an "
            "older version of HGPAKtool for at least partially working (re)packing."
        )


if __name__ == "__main__":
    run()
