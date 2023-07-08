from ctypes import cdll, c_char_p, create_string_buffer
import os.path as op


#TODO:
# - Add the compression levels as an enum
# - Add the compression algorithms as an enum
# https://gist.github.com/Nukem9/4ab163e7d38ae22c09be8a31586a6edf


class OodleDecompressionError(Exception):
    pass


class OodleCompressor():
    """
    Oodle decompression implementation.
    Requires Windows and the external Oodle library.
    """

    def __init__(self, library_path: str) -> None:
        """
        Initialize instance and try to load the library.
        """
        if not op.exists(library_path):
            raise Exception("Could not open Oodle DLL, make sure it is configured correctly.")

        try:
            self.handle = cdll.LoadLibrary(library_path)
        except OSError as e:
            raise Exception(
                "Could not load Oodle DLL, requires Windows and 64bit python to run."
            ) from e

    def compress(self, payload: bytes, size: int) -> bytes:
        # Overestimate the required buffer by creating one the same size as the
        # input file as we should be able to safely assume the compressed file
        # will not be larger than the original file.
        output = create_string_buffer(size)

        # OodleLZ_Compress arguments:
        # 0: compressor     which OodleLZ variant to use in compression
        # 1: rawBuf         raw data to compress
        # 2: rawLen         number of bytes in rawBuf to compress
        # 3: compBuf        pointer to write compressed data to ; should be at least $OodleLZ_GetCompressedBufferSizeNeeded
        # 4: level          OodleLZ_CompressionLevel controls how much CPU effort is put into maximizing compression
        # 5: pOptions       (optional) options; if NULL, $OodleLZ_CompressOptions_GetDefault is used
        # 6: dictionaryBase (optional) if not NULL, provides preceding data to prime the dictionary; must be contiguous with rawBuf, the data between the pointers _dictionaryBase_ and _rawBuf_ is used as the preconditioning data.  The exact same precondition must be passed to encoder and decoder.
        # 7: lrm            (optional) long range matcher
        # 8: scratchMem     (optional) pointer to scratch memory
        # 9: scratchSize    (optional) size of scratch memory (see $OodleLZ_GetCompressScratchMemBound)
        ret = self.handle.OodleLZ_Compress(
            9,                  # compressor
            payload,            # rawBuf
            size,               # rawLen
            output,             # compBuf
            6,                  # level
            None,               # pOptions
            None,               # dictionaryBase
            None,               # lrm
            None,               # scratchMem
            None,               # scratchSize
        )
        if ret != -1:
            return output[:ret]

    def decompress(self, payload: bytes, size: int, output_size: int) -> bytes:
        """
        Decompress the payload using the given size.
        """
        output = create_string_buffer(output_size)

        # OodleLZ_Decompress arguments:
        # 0:  compBuf           pointer to compressed data
        # 1:  compBufSize       number of compressed bytes available (must be greater or equal to the number consumed)
        # 2:  rawBuf            pointer to output uncompressed data into
        # 3:  rawLen            number of uncompressed bytes to output
        # 4:  fuzzSafe          (optional) should the decode fail if it contains non-fuzz safe codecs?
        # 5:  checkCRC          (optional) if data could be corrupted and you want to know about it, pass OodleLZ_CheckCRC_Yes
        # 6:  verbosity         (optional) if not OodleLZ_Verbosity_None, logs some info
        # 7:  decBufBase        (optional) if not NULL, provides preceding data to prime the dictionary; must be contiguous with rawBuf, the data between the pointers _dictionaryBase_ and _rawBuf_ is used as the preconditioning data.   The exact same precondition must be passed to encoder and decoder.  The decBufBase must be a reset point.
        # 8:  decBufSize        (optional) size of decode buffer starting at decBufBase, if 0, _rawLen_ is assumed
        # 9:  fpCallback        (optional) OodleDecompressCallback to call incrementally as decode proceeds
        # 10: callbackUserData  (optional) passed as userData to fpCallback
        # 11: decoderMemory     (optional) pre-allocated memory for the Decoder, of size _decoderMemorySize_
        # 12: decoderMemorySize (optional) size of the buffer at _decoderMemory_; must be at least $OodleLZDecoder_MemorySizeNeeded bytes to be used
        # 13: threadPhase       (optional) for threaded decode; see $OodleLZ_About_ThreadPhasedDecode (default OodleLZ_Decode_Unthreaded)
        ret = self.handle.OodleLZ_Decompress(
            c_char_p(payload),  # compBuf
            size,               # compBufSize
            output,             # rawBuf
            output_size,        # rawLen
            0,                  # fuzzSafe
            0,                  # checkCRC
            0,                  # verbosity
            None,               # decBufBase
            None,               # decBufSize
            None,               # fpCallback
            None,               # callbackUserData
            None,               # decoderMemory
            None,               # decoderMemorySize
            3,                  # threadPhase
        )

        # Make sure the result length matches the given output size
        if ret != output_size:
            raise OodleDecompressionError(
                "Decompression failed ret=0x{:x} output_size=0x{:x}".format(ret, output_size)
            )

        return output.raw
