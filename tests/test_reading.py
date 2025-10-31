import os
import os.path as op
import shutil
from pathlib import Path
from typing import Literal

import pytest

from hgpaktool import HGPAKFile
from hgpaktool.api import InvalidFileException

DATA_DIR = op.join(op.dirname(__file__), "data")


# TODO: Add switch?
plat_map = {
    "windows": "pc",
    "mac": "macos",
}


def get_files(fpath: os.PathLike) -> list[str]:
    file_list = []
    for root, _, files in os.walk(fpath):
        for file in files:
            file_list.append(os.path.join(root, file))
    return file_list


@pytest.mark.parametrize("platform", ("windows", "mac"))
def test_read(tmp_path: Path, platform: Literal["windows", "mac"]):
    with HGPAKFile(op.join(DATA_DIR, f"NMSARC.MeshPlanetSKY.{platform}.pak"), platform) as pak:
        assert len(pak.filenames) == 6
        # Extract the files to a temporary directory and analyse it
        pak.unpack(tmp_path)
        assert len(get_files(tmp_path)) == 6
        shutil.rmtree(tmp_path / "models")
        pak.unpack(tmp_path, upper=True)
        files = get_files(tmp_path)
        assert len(files) == 6
        # For each file ensure the base path component up to the extraction path has its capitalization
        # retained. Then check the filename itself is uppercase.
        for fpath in files:
            assert fpath.startswith(str(tmp_path))
            final_path = Path(fpath).relative_to(tmp_path)
            assert str(final_path).upper() == str(final_path)


@pytest.mark.parametrize("platform", ("windows", "mac"))
def test_filtered_extraction(platform: Literal["windows", "mac"]):
    with HGPAKFile(op.join(DATA_DIR, f"NMSARC.MeshPlanetSKY.{platform}.pak"), platform) as pak:
        assert len([x for x in pak.extract("*rainbowplane*")]) == 2
        assert len([x for x in pak.extract("*RAINBOWPLANE*")]) == 2
        assert len([x for x in pak.extract(["*rainbowplane*", "*skycube*"])]) == 4
        assert (
            len([x for x in pak.extract(f"models/planets/sky/skysphere.geometry.mbin.{plat_map[platform]}")])
            == 1
        )
        assert (
            len(
                [
                    x
                    for x in pak.extract(
                        f"MODELS/PLANETS/SKY/SKYSPHERE.GEOMETRY.MBIN.{plat_map[platform].upper()}"
                    )
                ]
            )
            == 1
        )


def test_invalid_pak():
    with pytest.raises(InvalidFileException):
        with HGPAKFile(op.join(DATA_DIR, "NMSARC.MeshPlanetSKY.invalid.pak")):
            pass


# TODO: Add test for switch pak's.
