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

Currently you need to run the `decompress.py` file directly with python (eg. `python3.9 decompress.py`). Note that python 3.9+ is required to run this code.
Inside the file at the bottom you may specify the path to the .pak file that you want to extract all the files for.
Currently this is the only easy thing to do with the script, but I'll hopefully add more functionality in the future.
