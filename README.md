# HGPAK tool

This tool is able to read .pak files for the game No Man's Sky on all platforms.
Note that this tool only works on .pak files after the NMS 5.50 (Worlds Part II) update.

## Installation

HGPAKtool can be installed in one of two ways:

### Precompiled binary

Precompiled binaries are provided for both windows and linux OS's (macOS can be created on request but someone will need to test it).
These can be found [here](https://github.com/monkeyman192/HGPAKtool/releases).

These binaries contain everything you need to run the tool - no need to install python or any other dependencies.

### Python library

If you wish to use HGPAKtool from code, the wheel is published on [pypi](https://pypi.org/project/HGPAKtool/).

Install by running `python -m pip install hgpaktool`.

If you would like to support the json5 format, there is an optional dependency which can be included:

`python -m pip install hgpaktool[json5]`

**API docs will come soon.**

## Usage

***Note:*** *The following is only relevant if using a precompiled binary*

For decompiling files on PC, mac or linux, the tool will automatically detect the platform, however if you want to decompile .pak files from a specific platform provide the `--platform` flag (see `--help` for extra details).

If you are unpacking files from a switch you will requires the Oodle dll which is not particularly freely available.
This code contains a way to download it, however you should always verify that the downloaded dll contains no malicious content by scanning it with a virus checker.
I do not own the link contained in the code and take no responsibility for any issues caused by using the dll which it downloads.
I have tested it and it seems fine, but you should check yourself.
To use this, specify `--platform switch` when running `hgpaktool`.

### Drag and drop usage

The easiest way to use HGPAKtool is by dragging the files you wish to decompile directly onto the binary.
This will unpack them in the same directory as the .pak files but under a folder called `EXTRACTED`.

To unpack all of the games' files, simply drag the `PCBANKS` folder onto the binary and go and make a drink as this will take a little bit of time to complete.

### Command line usage

**Note** If you installed via python above instead of a precompiled binary, the following section can be used, however you can drop the `.exe` part and simply call `hgpaktool` directly as the installation will build this binary in your python scripts folder.

If you want more control, it is recommended that you run the script directly like so:

`hgpaktool.exe -U <path to PCBANKS folder>`

If you do this, you can provide a number of other options such as the `-f` flag which will export only the files which match the pattern provided.
So for example to export all files which contain the phrase "debris", you would do:

`hgpaktool.exe -U -f="*debris*" <path to PCBANKS folder>`

Multiple `-f` flags can be provided to filter multiple sets of files out. Note that these combine additively (as in `-f="*debris*" -f="*crystal*"` will extract all files containing `debris` in their path AND all files containing `crystal` in their path).
The flag can also be used to pull out one or more specific files if the complete path to the file (within the pak files) is known/provided.

For a complete list of the possible options, run `hgpaktool.exe --help`

# Re-packing files

Note that repacking files currently has very limited support.

On windows it is not required to make mods so is currently not implemented (see the [community docs](http://tinyurl.com/nmsmodding550) regarding modding for how to add mods to the game).

Re-packing switch games does work but may have some issues, so should be considered not fully functional for now. I don't have any way to test re-packed .pak file on a modded switch so unless someone is willing to contribute some effort to properly test it and improve it it's likely it will stay this way.
