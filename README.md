# HGPAK tool

This code is designed to read .pak files for the game No Man's Sky on Mac and Nintendo Switch.
For reading .pak files on PC/PS4/PS5 there are a number of other tools

**NOTE:** This code is a heavy WIP. It is missing many features and is mostly just meant to be used to help others write code in a more convenient for end-users language.
To that end the code contained within is freely available for modification/conversion to any other language you wish to translate it to.

This code was originally writeen to handle pak files compressed with the oodle compression algorithm. It turns out that on mac this isn't used even though the HGPAK format header looks the same. Until it's confirmed that the compression on switch is different I'll leave the Oodle code in, but for now the following paragraph is not important.
Also, this code requires the Oodle dll which is not particularly freely available.
This code contains a way to download it, however you should always verify that the downloaded dll contains no malicious content by scanning it with a virus checker.
I do not own the link contained in the code and take no responsibility for any issues caused by using the dll which it downloads.
I have tested it and it seems fine, but you should check yourself.

## Usage:

First, install the [lz4](https://pypi.org/project/lz4/) python package:
```
python -m pip install lz4
```
(installation may vary system to system depending on your python installation)

Note that this code requires python 3.9+ to run. If you have a lower version you'll need to install a newer one.

### Drag and drop functionality.

The `HGPAKTool.bat` file provides drag and drop functionality.
To utilise this, first open a console and enter `python --version`. This should show a version that is at least 3.9.X.
If it doesn't show a version this high, or has an error that `python` is invalid, you may need to install python, or find the command which calls python on your system. Other options are `python3`, or `python3.X` where `X` is the installed version you have.
If you have one of the `python3` commands working, but not `python`, then you will need to change the value in `HGPAKTool.bat` to be the one which works.

Dragging one or more .pak files onto the .bat should unpack them in the current directory under a folder called `EXTRACTED`. Note that this name currently cannot be changed.

Dragging multiple files which are not .pak files onto the bat will pack them up and compress them for MAC.
NOTE: compressed switch packing currently not supported.
There is currently also an issue with repacking archives when compressed.
