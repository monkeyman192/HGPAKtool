import os
import os.path as op
import shutil
from pathlib import Path

from hgpaktool import HGPAKFile

DATA_DIR = op.join(op.dirname(__file__), "data")


def get_files(fpath: os.PathLike) -> list[str]:
    file_list = []
    for root, _, files in os.walk(fpath):
        for file in files:
            file_list.append(os.path.join(root, file))
    return file_list


def test_read(tmp_path: Path):
    with HGPAKFile(op.join(DATA_DIR, "NMSARC.MeshPlanetSKY.pak")) as pak:
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


def test_filtered_extraction():
    with HGPAKFile(op.join(DATA_DIR, "NMSARC.MeshPlanetSKY.pak")) as pak:
        assert len([x for x in pak.extract("*rainbowplane*")]) == 2
        assert len([x for x in pak.extract("*RAINBOWPLANE*")]) == 2
        assert len([x for x in pak.extract(["*rainbowplane*", "*skycube*"])]) == 4
        assert len([x for x in pak.extract("models/planets/sky/skysphere.geometry.mbin.pc")]) == 1
        assert len([x for x in pak.extract("MODELS/PLANETS/SKY/SKYSPHERE.GEOMETRY.MBIN.PC")]) == 1
