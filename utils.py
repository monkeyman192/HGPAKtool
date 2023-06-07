# import lzma
import os.path as op
from platform import system as os_name
import urllib.request


OSNAMEMAP = {'Windows': 'WIN',
             'Linux': 'LNX',
             'Darwin': 'MAC'}


class OSConstMap():
    """ Class which provides values of constants in an OS-dependent manner """
    def __init__(self):
        self.os = OSNAMEMAP.get(os_name(), 'WIN')

    def __setattr__(self, name, value):
        self.__dict__[name] = value

    def __getattr__(self, name):
        """ Retreive the required value

        Names will be suffixed by '_<OS>', so we want to just get the os
        dependent version
        If the variable is assigned in a non-OS-dependent manner (ie. no
        suffix then this will not be called and the value will be retreived
        directly)

        Parameters
        ----------
        name : str
            Name of the variable without an OS identifier
        """
        os_dep_name = name + '_' + self.os
        return self.__dict__[os_dep_name]


OSCONST = OSConstMap()

# Binaries urls
OSCONST.LIB_URL_WIN = "https://raw.githubusercontent.com/WorkingRobot/OodleUE/main/Engine/Source/Programs/Shared/EpicGames.Oodle/Sdk/2.9.3/win/redist/oo2core_9_win64.dll"
# OSCONST.LIB_URL_WIN = "https://origin.warframe.com/origin/E926E926/Tools/Oodle/x64/final/oo2core_9_win64.dll.F2DB01967705B62AECEF3CD3E5A28E4D.lzma"
OSCONST.LIB_URL_MAC = "https://raw.githubusercontent.com/WorkingRobot/OodleUE/main/Engine/Source/Runtime/OodleDataCompression/Sdks/2.9.8/lib/Mac/liboo2coremac64.2.9.8.dylib"

# Binaries names
OSCONST.LIB_NAME_WIN = "oo2core_9_win64.dll"
OSCONST.LIB_NAME_MAC = "liboo2coremac64.2.9.8.dylib"


def download_dll(out_dir: str = ""):
    """ Simple script to download the Oodle binary. """
    with urllib.request.urlopen(OSCONST.LIB_URL) as f:
        print(f"Downloading Oodle from {OSCONST.LIB_URL}")
        data = f.read()
        # decompressed = lzma.decompress(f.read())
    out_path = op.join(out_dir, OSCONST.LIB_NAME)
    if op.exists(out_path):
        inp = input("This file already exists. Do you want to override? (Y/N): ")
        if inp.lower() != "y":
            return
    with open(out_path, "wb") as f:
        f.write(data)
    print(f"Wrote file to {op.join(out_dir, OSCONST.LIB_NAME)}")


if __name__ == "__main__":
    # For now, running this script as main will simply download the lib into
    # your cwd.
    download_dll()
