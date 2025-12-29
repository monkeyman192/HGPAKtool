import os.path as op
import shutil
from pathlib import Path
from typing import Literal

import pytest
from utils import get_files, plat_map

from hgpaktool import HGPAKFile
from hgpaktool.api import InvalidFileException
from hgpaktool.utils import normalise_path, parse_manifest

DATA_DIR = op.join(op.dirname(__file__), "data")


@pytest.mark.parametrize("platform", ("windows", "mac", "linux"))
def test_unpack(tmp_path: Path, platform: Literal["windows", "mac", "linux"]):
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


@pytest.mark.parametrize("platform", ("windows", "mac", "linux"))
def test_unpack_with_manifest(tmp_path: Path, platform: Literal["windows", "mac", "linux"]):
    with HGPAKFile(op.join(DATA_DIR, f"NMSARC.MeshPlanetSKY.{platform}.pak"), platform) as pak:
        assert len(pak.filenames) == 6
        # Extract the files to a temporary directory and analyse it
        pak.unpack(tmp_path, write_manifest=True)
        files = get_files(tmp_path)
        assert len(files) == 7
        # Find the manifest file
        manifest_fpath = None
        for fpath in files:
            if fpath.endswith(".manifest"):
                manifest_fpath = fpath
        assert manifest_fpath is not None
        assert op.exists(manifest_fpath)

        manifest_contents = parse_manifest(manifest_fpath)
        # Remove the base path and normalise the paths of the real files and check that they match the
        # manifest
        norm_paths = []
        for fpath in files:
            if fpath != manifest_fpath:
                norm_paths.append(normalise_path(op.relpath(fpath, tmp_path)))
        assert len(norm_paths) == 6
        assert set(norm_paths) == set(manifest_contents)


@pytest.mark.parametrize("platform", ("windows", "mac", "linux"))
def test_filtered_extraction(platform: Literal["windows", "mac", "linux"]):
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
