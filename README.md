# HGPAK tool

This code is designed to read .pak files for the game No Man's Sky on all platforms.
For decompiling files on PC or mac, the tool will automatically detect the platform, however if you want to decompile .pak files from a specific platform provide the `--platform` flag (see `--help` for extra details).

If you are unpacking files from a switch you will requires the Oodle dll which is not particularly freely available.
This code contains a way to download it, however you should always verify that the downloaded dll contains no malicious content by scanning it with a virus checker.
I do not own the link contained in the code and take no responsibility for any issues caused by using the dll which it downloads.
I have tested it and it seems fine, but you should check yourself.

## Usage:

First, install the [zstandard](https://pypi.org/project/zstandard/) python package:
```
python -m pip install zstandard
```
(installation may vary system to system depending on your python installation)

Note that this code requires python 3.9+ to run. If you have a lower version you'll need to install a newer one.

**Note:** If you are wanting to extract .pak files from a mac install of NMS, you will instead require the [lz4](https://pypi.org/project/lz4/) package.

### Drag and drop functionality.

The `HGPAKTool.bat` file provides drag and drop functionality.
To utilise this, first open a console and enter `python --version`. This should show a version that is at least 3.9.X.
If it doesn't show a version this high, or has an error that `python` is invalid, you may need to install python, or find the command which calls python on your system. Other options are `python3`, or `python3.X` where `X` is the installed version you have.
If you have one of the `python3` commands working, but not `python`, then you will need to change the value in `HGPAKTool.bat` to be the one which works.

Dragging one or more .pak files onto the .bat should unpack them in the current directory under a folder called `EXTRACTED`, or whatever folder is provided by the `-O` flag.

To unpack your entire PCBANKS folder simply drag the entire folder onto the .bat file.

If you want more control, it is recommended that you run the script directly like so:

`python hgpaktool/hgpaktool.py -U <path to PCBANKS folder>`

If you do this, you can provide a number of other options such as the `-f` flag which will export only the files which match the pattern provided.
So for example to export all files which contain the phrase "debris", you would do:

`python hgpaktool/hgpaktool.py -U -f="*debris*" <path to PCBANKS folder>`

Multiple `-f` flags can be provided to filter multiple sets of files out. Note that these combine additively.
The flag can also be used to pull out a single specific file if one provided the complete path to the file (if it is known).

For a complete list of the possible options, run `python hgpaktool/hgpaktool.py --help`

Dragging multiple files which are not .pak files onto the bat will pack them up and compress them.
NOTE: compressed switch packing currently not supported.
There is currently also an issue with repacking archives when compressed.
