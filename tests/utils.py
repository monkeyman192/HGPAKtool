import os

# TODO: Add switch?
plat_map = {
    "windows": "pc",
    "linux": "pc",
    "mac": "macos",
}


def get_files(fpath: os.PathLike) -> list[str]:
    file_list = []
    for root, _, files in os.walk(fpath):
        for file in files:
            file_list.append(os.path.join(root, file))
    return file_list
