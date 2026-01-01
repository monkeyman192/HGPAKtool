import os.path as op
import shutil
from pathlib import Path
from typing import Literal

import pytest
from utils import get_files

from hgpaktool import HGPAKFile

DATA_DIR = op.join(op.dirname(__file__), "data")


@pytest.mark.parametrize("fname,filecount", [("globals", 39), ("MeshPlanetSKY", 6)])
@pytest.mark.parametrize("platform", ("windows", "mac"))
def test_repack(tmp_path: Path, fname: str, filecount: int, platform: Literal["windows", "mac"]):
    # Ensure that repacked files are the same as the original

    # First, move the file to the temporary directory so that we can rename the file without the platform.
    shutil.copyfile(op.join(DATA_DIR, f"NMSARC.{fname}.{platform}.pak"), tmp_path / f"NMSARC.{fname}.pak")
    with HGPAKFile(tmp_path / f"NMSARC.{fname}.pak", platform) as pak:
        assert len(pak.filenames) == filecount
        # Extract the files to a temporary directory and analyse it.
        pak.unpack(tmp_path, write_manifest=True)

    files = get_files(tmp_path)
    assert len(files) == filecount + 1 + 1  # n files in pak. +1 for manifest and +1 for the original pak.

    # Rename the original pak file so that we can do a byte compare.
    shutil.move(tmp_path / f"NMSARC.{fname}.pak", op.join(tmp_path, f"NMSARC.{fname}.pak.original"))

    assert not op.exists(tmp_path / f"NMSARC.{fname}.pak")

    # Find the manifest file.
    manifest_fpath = None
    for fpath in files:
        if fpath.endswith(".manifest"):
            manifest_fpath = fpath
    assert manifest_fpath is not None
    assert op.exists(manifest_fpath)

    HGPAKFile.repack(manifest_fpath, platform=platform)
    assert op.exists(tmp_path / f"NMSARC.{fname}.pak")
    with open(tmp_path / f"NMSARC.{fname}.pak", "rb") as f:
        repacked_data = f.read()

    with open(tmp_path / f"NMSARC.{fname}.pak.original", "rb") as f:
        original_data = f.read()

    assert repacked_data == original_data
