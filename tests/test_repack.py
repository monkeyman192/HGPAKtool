import os.path as op
from pathlib import Path
from typing import Literal

import pytest
from utils import get_files

from hgpaktool import HGPAKFile

DATA_DIR = op.join(op.dirname(__file__), "data")


@pytest.mark.parametrize("compress", (True, False))
@pytest.mark.parametrize("platform", ("windows", "mac"))
def test_repack(tmp_path: Path, platform: Literal["windows", "mac"], compress: bool):
    with HGPAKFile(op.join(DATA_DIR, f"NMSARC.globals.{platform}.pak"), platform) as pak:
        assert len(pak.filenames) == 39
        # Extract the files to a temporary directory and analyse it
        pak.unpack(tmp_path, write_manifest=True)

        files = get_files(tmp_path)
        assert len(files) == 40

        # Find the manifest file
        manifest_fpath = None
        for fpath in files:
            if fpath.endswith(".manifest"):
                manifest_fpath = fpath
        assert manifest_fpath is not None
        assert op.exists(manifest_fpath)

    HGPAKFile.repack(manifest_fpath, compress=compress, platform=platform)
    assert op.exists(tmp_path / f"NMSARC.globals.{platform}.pak")
