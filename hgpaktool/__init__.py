from importlib.metadata import PackageNotFoundError, version

from .api import HGPAKFile  # noqa

try:
    __version__ = version("hgpaktool")
except PackageNotFoundError:
    __version__ = None
