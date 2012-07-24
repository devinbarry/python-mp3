#
# mp3.py -- MP3-frame meta-data parser
# Copyright (C) 2003-2004  Sune Kirkeby
#               2012       Lorenz Bauer
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 59 Temple Place, Suite 330, Boston, MA  02111-1307  USA
#

"""Routines for parsing MP3-frame meta-data.

This is a collection of routines for parsing MP3-files and extracting
raw frame-data and meta-data (such as frame bitrates)."""

from __future__ import generators
from collections import namedtuple
import struct
from _bitpack import bitpack_into, formatstr as bitpack_formatstr, invalid_input_error
from _crc16 import crc16
from _buffer import ZeroCopyBuffer
import os
import math

__all__ = ['APEFrame', 'Channelmode', 'Frame', 'Header', 'ID3Frame', 'MP3Error', \
           'MP3FrameHeaderError', 'MPEGFrame', 'MetaFrame', 'RIFFFrame', 'Reader', \
           'XingFrame', 'ZeroCopyBuffer', 'framedata', 'frameheader', 'framelen', \
           'frames', 'good_data']

class MP3Error(Exception):
    """I signal a generic error related to MP3-data."""
    pass
class MP3FrameHeaderError(MP3Error):
    """I signal that there was an error parsing the meta-data in an
    MP3-frame header."""
    pass

class _InvalidFrame(Exception):
    """I signal that the parsing of the current frame type failed."""
    pass

class Channelmode:
    """Read-only convenience container for the different MPEG channel modes. See
    http://www.mp3-tech.org/programmer/frame_header.html for further information."""
    STEREO, JOINT_STEREO, DUAL_CHANNEL, MONO = range(4)

class Frame(object):
    """Basic frame object, all other frametypes extend from this."""
    _buffer = None
    length = None

    @property
    def view(self):
        """view() -> memoryview
        
        Returns a memoryview of the frame's data.
        """
        return self._buffer.view()
    
    def bytes(self):
        """bytes() -> bytearray()
        
        Returns a copy of the frame's data.
        """
        return self._buffer.bytes()

    def __eq__(self, other):
        if isinstance(other, Frame):
            return self._buffer == other._buffer
        else:
            return self._buffer == other

    def _frame_assembled(self):
        """Implement this method if you need to do sth. as soon
        as the frames buffer is available."""
        pass

    def append(self, buf):
        """append(buf) -> nothing
        
        Append data from buf to this frame, up to the frame's
        length. Expects a ZeroCopyBuffer as input.
        """
        self._buffer = ZeroCopyBuffer(self.length)
        buf.delete(self._buffer.extend(buf))
        
        if (len(self._buffer) < self.length):
            assert(len(buf) == 0)
            self._buffer.fill(buf._fileobj, completely=True)
        
        assert(len(self._buffer) == self.length)
        self._frame_assembled()

class MetaFrame(Frame):
    """Parent class for all meta-data related frames."""
    pass

class ID3Frame(MetaFrame):
    """Represents and ID3v1/2 frame (storing file meta-data)."""
    V1, V2 = range(2)

    version = None

    def __init__(self, buf, fileobj = None, offset = 0, strict = False):
        if buf.startswith('TAG', offset):
            self.version = self.V1
            self.length  = 128
        elif buf.startswith('ID3', offset):
            self.version = self.V2

            if len(buf) < 10:
                raise MP3Error, 'need at least 10 bytes of data'

            self.length = (buf[offset + 6] << 21) + (buf[offset + 7] << 14) + \
                (buf[offset + 8] << 7) + (buf[offset + 9]) + 10
        else:
            raise _InvalidFrame

class APEFrame(MetaFrame):
    """Represents and APETAGv1/2 frame (storing file meta-data)."""
    V1, V2 = range(2)
    _HEADER_SIZE = 32

    version = None

    def __init__(self, buf, fileobj = None, offset = 0, strict = False):
        if not buf.startswith('APETAGEX', offset):
            raise _InvalidFrame

        version, length = buf.unpack('<II', offset + 8)
        if version == 2000: self.version = self.V2
        elif version == 1000: self.version = self.CV1

        self.length = length + self._HEADER_SIZE

class RIFFFrame(Frame):
    """Represents a RIFF frame (commonly used for compatibility with broken Windows players)."""
    def __init__(self, buf, fileobj = None, offset = 0, strict = False):
        if buf.startswith('RIFF', offset) and buf.startswith('WAVE', offset + 8):
            if fileobj: fileobj._has_riff_header = True
            self.length = 12

        elif fileobj is None or fileobj._has_riff_header:
            if buf.startswith('data', offset):
                self.length = 8

            elif buf.startswith('fmt ', offset):
                self.length, fmt = buf.unpack('<IH', offset + 4)
                self.length += 8

                if fmt != 0x55: # Not MPEG 1 Layer III data
                    raise MP3Error('encountered RIFF file with non MPEG 1 Layer III data, fmt = 0x%x' % fmt)

            elif buf.startswith('fact', offset):
                self.length, = buf.unpack('<I', offset + 4)
                self.length += 8

            else:
                raise _InvalidFrame
        else:
            raise _InvalidFrame

class MPEGFrame(Frame):
    header = None
    
    """Represents an MPEG frame (storing raw audio data)."""
    def __init__(self, buf, fileobj = None, offset = 0, strict = False):
        try:
            header = Header(buf, offset)
        except MP3FrameHeaderError:
            raise _InvalidFrame

        self.header = header

        version, layer = header.version, header.layer
        if strict and fileobj and \
            ((fileobj._mpeg_version and version != fileobj._mpeg_version) or \
            (fileobj._mpeg_layer and layer != fileobj._mpeg_layer)):
            raise _InvalidFrame

        self.length = self._calculate_length(header)

    def _frame_assembled(self):
        self.header.update(self._buffer)

    def commit_header(self):
        """commit_header() -> nothing
        
        Stores a modified header in the frames data."""
        self._buffer.replace(self.header.bytes())

    @classmethod
    def _calculate_length(cls, header):
        version, layer = header.version, header.layer
        bitrate, samplingrate = header.bitrate, header.samplingrate
        padding = header.padding

        if version == 1:
            if layer == 1:
                mul, slot = 12, 4
            else:
                mul, slot = 144, 1
        else:
            if layer == 1:
                mul, slot = 240, 4
            else:
                mul, slot = 72, 1

        return ((mul * bitrate * 1000 / samplingrate) + (padding * slot)) * slot

class XingFrame(MPEGFrame):
    """Represents a Xing frame (storing VBR encoding information)."""
    
    # TODO: Be able to fix corrupt Xing frames
    
    _MIN_HEADER_SIZE = 4 + 4

    def __init__(self, buf, fileobj = None, offset = 0, strict = False):
        if fileobj and fileobj._has_xing_header == False:
            raise _InvalidFrame

        super(XingFrame, self).\
            __init__(buf, fileobj, offset=offset, strict=strict)

        header = self.header

        # Some implementations write the Xing Header at the wrong position if the frame
        # has CRC enabled. Check both places.
        offset += header.length(True)

        if buf.startswith('Xing', offset) or buf.startswith('Info', offset) or \
            (header.crc and (buf.startswith('Xing', offset + 2) or \
            buf.startswith('Info', offset + 2))):

            if fileobj: fileobj._has_xing_header = True

            self.has_vbr_quality, self.has_toc, self.has_total_size, self.has_total_frames = \
                [bool(buf[offset+7] & 1 << 3 - i) for i in xrange(4)]

            length = header.length() + self._MIN_HEADER_SIZE
            if self.has_vbr_quality:  length += 4
            if self.has_toc:          length += 100
            if self.has_total_size:   length += 4
            if self.has_total_frames: length += 4

            self.xing_length = length

            assert(self.length >= length)

        else:
            if fileobj: fileobj._has_xing_header = False
            raise _InvalidFrame

    def _frame_assembled(self):
        super(XingFrame, self)._frame_assembled()

        offset = self.header.length() + self._MIN_HEADER_SIZE

        if self.has_total_frames:
            self.total_frames, = self._buffer.unpack('>I', offset)
            offset += 4

        if self.has_total_size:
            self.total_size, = self._buffer.unpack('>I', offset)
            offset += 4

        if self.has_toc:
            self.toc = list(self._buffer.unpack('>100B', offset))
            offset += 100

        if self.has_vbr_quality:
            self.vbr_quality, = self._buffer.unpack('>I', offset)
            offset += 4

    def seekpoint(self, percent, file_size = None):
        """seekpoint(percent, file_size = None) -> byte offset in file
        
        Returns a byte offset for the specified file_size, according to
        the Xing header specification."""
        percent *= 1.0
        file_size = file_size or self.total_size

        if percent > 100.0: percent = 100.0
        elif percent < 0.0: percent = 0.0

        index = int(math.floor(percent))
        if index > 99: index = 99

        factor_a = self.toc[index]
        factor_b = (index < 99) and self.toc[index+1] or 256.0

        factor = factor_a + (factor_b - factor_a) * (percent - index)
        return int((1.0/256.0) * factor * file_size)


class Reader(object):
    """Reader object representing a stream of MPEG/ID3/APE/RIFF frames."""
    _FRAME_TYPES = (XingFrame, MPEGFrame, RIFFFrame, ID3Frame, APEFrame)
    _MIN_FRAME_SIZE = 38

    _offset = 0
    _has_riff_header = False
    _has_xing_header = None

    _mpeg_version = None
    _mpeg_layer = None

    _buffer_size = None

    def __init__(self, inobj, buffer_size=8192):
        self._inobj = inobj
        self._buffer_size = buffer_size

    def frames(self, skip_invalid_data = True, emit_meta_frames = True, \
        emit_riff_frames = True, emit_id3_frames = True, emit_ape_frames = True):
        """frames(skip_invalid_data = True, emit_meta_frames = True, \
            emit_riff_frames = True, emit_id3_frames = True, emit_ape_frames = True) -> frame data
        
        Reads frames one-by-one, according to the method's arguments.
        Raises an MP3Error if invalid data is encountered and ingore_invalid_data
        is False.
        """
        in_sync = True

        try:
            buf = ZeroCopyBuffer(self._buffer_size, self._inobj)
            buf.fill()

            while len(buf) > 4: # We need at least 4 bytes for our shortest header
                # Try to parse a frame
                frame = None
                for frame_class in self._FRAME_TYPES:
                    try:
                        frame = frame_class(buf, self, strict=not in_sync)
                        break
                    except _InvalidFrame:
                        pass

                if frame and not in_sync:
                    # Recover from lost sync
                    try:
                        buf.fill(at_least = frame.length + 12)

                        # See if there is a consequent valid frame
                        for frame_class in self._FRAME_TYPES:
                            try:
                                frame_class(buf, self, offset=frame.length, strict=True)
                                in_sync = True
                                break
                            except _InvalidFrame:
                                pass
    
                        frame = in_sync and frame or None
                    except EOFError:
                        # Not enough data left to check the next frame, accept it anyways
                        pass

                if frame:
                    # Consumed data is removed from the buffer in Frame.append()
                    frame.append(buf)

                    if (isinstance(frame, ID3Frame) and (emit_meta_frames or emit_id3_frames)) or \
                        (isinstance(frame, APEFrame) and (emit_meta_frames or emit_ape_frames)) or \
                        (isinstance(frame, RIFFFrame) and emit_riff_frames) or \
                        isinstance(frame, MPEGFrame):
                        yield frame
                else:
                    in_sync = False

                    if not skip_invalid_data:
                        raise MP3Error('encountered invalid data')

                    buf.delete(1)

                if len(buf) < 12:
                    buf.fill(self._inobj)
        except EOFError:
            if not skip_invalid_data:
                raise MP3Error('encountered invalid data')
        finally:
            del buf

class Header(object):
    """Represents an MPEG frame header."""
    _BITRATES = [
        # Version 1
        [
            # Layer I
            [32, 64, 96, 128, 160, 192, 224, 256, 288, 320, 352, 384, 416, 448],
            # Layer II
            [32, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 384],
            # Layer III
            [32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320]
        ],
        # Version 2/.5
        [
            # Layer I
            [32, 48, 56, 64, 80, 96, 112, 128, 144, 160, 176, 192, 224, 256],
            # Layer II & III
            [8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160],
        ],
    ]
    _BITRATES[1].append(_BITRATES[1][1])

    _SAMPLINGRATES = [
        # Version 1
        [44100, 48000, 32000],
        # Version 2
        [22050, 24000, 16000],
    ]

    _SIDE_INFO_SIZE = [
        [32, 17],
        [17, 9]
    ]

    _HEADER = (
        ('i:11=0x7ff', 'sync'),
        ('i:2',        'version'),
        ('i:2',        'layer'),
        ('b',          'crc'),
        ('i:4',        'bitrate'),
        ('i:2',        'samplingrate'),
        ('b',          'padding'),
        ('b',          'private'),
        ('i:2',        'channelmode'),
        ('i:2',        'modeextension'),
        ('b',          'copyright'),
        ('b',          'original'),
        ('i:2',        'emphasis')
    )

    _FORMAT, _FIELDS = zip(*_HEADER)
    _FORMAT = bitpack_formatstr(_FORMAT)

    _crc16 = None
    _side_info = None

    def __init__(self, buf, offset = 0):
        """__init__(buf, offset = 0)
        
        Read an MPEG frame header from buf at position offset. Raises
        an MP3FrameHeaderError if no valid header can be found.
        """
        if buf is None:
            self.__dict__.update(dict.fromkeys(self._FIELDS))
        else:
            try:
                self.__dict__.update(zip(self._FIELDS, \
                    buf.bitunpack(self._FORMAT, offset)))
            except invalid_input_error:
                raise MP3FrameHeaderError('frame sync not found')

            for key in self._FIELDS:
                # This works as a basic validator
                getattr(self, key)
                
            self.update(buf, offset)

    def update(self, buf, offset = 0):
        """update(buf, offset = 0) -> nothing
        
        Updates the headers data from buf at position offset. This
        is useful if you instantiated the header from a minimal buffer
        (of 4 bytes) and now want to read the crc and side channel
        information from the buffer.
        """
        # Read up to max(side_chan_info) + crc
        length = len(buf)
        offset += 4 # Skip header

        if self.crc:
            if not self._crc16 and length >= offset + 2:
                self._crc16, = buf.unpack('>H', offset)
            offset += 2

        if not self._side_info and length >= offset + self.side_info_size():
            self._side_info = buf.bytes(offset, self.side_info_size())


    def bytes(self, include_crc = True):
        """bytes(include_crc = True) -> bytearray
        
        Returns a representation of the header in packed binary format.
        If include_crc is False, the CRC will be omitted regardless of
        the header.crc flag.
        """
        buf = bytearray(self._FORMAT.length)
        bitpack_into(self._FORMAT, buf, 0, *[self.__dict__[k] for k in self._FIELDS])

        pos = len(buf)
        buf.extend(self._side_info)

        if include_crc and self.crc:
            crc = crc16(memoryview(buf)[2:])
            buf[pos:pos] = 2 # Inserts two bytes at pos
            struct.pack_into('>H', buf, pos, crc)

        return buf

    def side_info_size(self):
        """side_info_size() -> length of side channel info
        
        Returns the length of the side channel informaton in bytes.
        """
        return self._SIDE_INFO_SIZE[self.version > 1][self.channelmode == Channelmode.MONO]

    def length(self, include_crc = True, include_side_info = True):
        """length(include_crc = True, include_side_info = True) -> length of the header
        
        Returns the length of the header in bytes.
        """
        return 4 + include_crc * self.crc * 2 +  include_side_info * self.side_info_size()

    def valid(self):
        """valid() -> bool
        
        Validates the frame's CRC if present. Returns True if the frame is not
        protected by a CRC.
        """
        if not self.crc:
            return True

        return self._crc16 == self.calculate_crc()

    def calculate_crc(self):
        """calculate_crc() -> CRC value
        
        Returns the frame's calculated CRC, based on the information present in
        the header. This is the value that _should_ be in the bytestream, not the
        one that is. Use header._crc16 to get this.
        """
        # TODO: Check for empty side info?
        # First two bytes of the header are skipped
        buf = self.bytes(include_crc = False)
        return crc16(memoryview(buf)[2:])

    def time(self):
        """time() -> running time in seconds
        
        Returns the frame's running time in seconds.
        """
        return (self.layer == 1) and (384.0 / 44100) or (1152.0 / 44100)

    @property
    def version(self):
        """MPEG Version. Possible values are 1, 2 and 2.5."""
        version = self.__dict__['version']

        if version == 0:
            return 2.5
        elif version == 1:
            raise MP3FrameHeaderError, 'unknown MPEG version (bad frame sync?)'
        else:
            return 4 - version

    @version.setter
    def version(self, value):
        if value == 2.5:
            value = 0
        elif value == 1 or value == 2:
            value = 4 - value
        else:
            raise MP3FrameHeaderError('invalid MPEG version: %d' % value)

        self.__dict__['version'] = value

    @property
    def layer(self):
        """MPEG Layer. Possible values are 1, 2, 3."""
        layer = self.__dict__['layer']

        if layer == 0:
            raise MP3FrameHeaderError, 'unknown Layer description'
        else:
            return 4 - layer

    @layer.setter
    def layer(self, value):
        value = int(value)

        if value >= 1 and value <= 3:
            value = 4 - value
        else:
            raise MP3FrameHeaderError('invalid Layer description: %d' % value)

        self.__dict__['layer'] = value

    @property
    def crc(self):
        """Is the frame protected by a CRC?"""
        return not self.__dict__['crc']

    @crc.setter
    def crc(self, val):
        self.__dict__['crc'] = not val

    @property
    def bitrate(self):
        """Frame's bitrate."""
        bitrate = self.__dict__['bitrate']

        if bitrate == 0xF or bitrate == 0x0:
            raise MP3FrameHeaderError, 'bad bitrate'

        return self._BITRATES[int(self.version)-1][self.layer-1][bitrate-1]

    @bitrate.setter
    def bitrate(self, value):
        try:
            self.__dict__['bitrate'] = \
                self._BITRATES[int(self.version)-1][self.layer-1].index(value) + 1
        except ValueError:
            raise MP3FrameHeaderError('invalid bitrate: %d' % value)

    @property
    def samplingrate(self):
        """Frame's samplingrate."""
        samplingrate = self.__dict__['samplingrate']
        version = self.version

        if samplingrate == 3:
            raise MP3FrameHeaderError, 'bad sampling-rate'

        if version == 2.5:
            return self._SAMPLINGRATES[int(version)-1][samplingrate] / 2
        else:
            return self._SAMPLINGRATES[int(version)-1][samplingrate]


    @samplingrate.setter
    def samplingrate(self, value):
        version = self.version

        if version == 2.5:
            value *= 2

        try:
            self.__dict__['samplingrate'] = \
                self._SAMPLINGRATES[int(version)-1].index(value)
        except ValueError:
            if version == 2.5: value /= 2
            raise MP3FrameHeaderError('invalid sampling-rate: %d' % value)

## OLD API

class _HeaderWrapper(tuple):
    """ A wrapper class providing tuple access to the new Header object.
    """
    _fields = ('version', 'layer', 'crc', 'bitrate', 'samplingrate', 'padding')

    header = None

    def __new__(cls, header):
        obj = tuple.__new__(cls, [getattr(header, k) for k in cls._fields])
        obj.header = header
        return obj

def _unwrap(wrapper):
    if isinstance(wrapper, _HeaderWrapper):
        return wrapper

    h = Header(None)
    h.version, h.layer, h.crc, h.bitrate, h.samplingrate, h.padding = wrapper

    return h

def frameheader(buf, offset):
    """frameheader(buf, i) -> header
    Parse the header of the MP3-frame found at offset i in buf.

    MP3-frame headers are tuples of

        (version, layer, crc, bitrate, samplingrate, padding)

    The fields returned in the header-tuple are mostly self-explaining,
    if you know MP3-files. There are a few pit-falls, though:

    The version is an integer for MP3-versions 1 and 2, but there
    exists an unofficial version 2.5 (which supports different bitrates
    and sampling rates than version 2), for which version is a float.

    The bitrate is returned in kbit/s (e.g. 128, 192).

    The sampling rate is returned in Hz (e.g. 44100)."""

    header = Header(buf, offset)
    return _HeaderWrapper(header)

def time(wrapper):
    """time(header) -> seconds

    Calculate the length in seconds of the MP3-frame given it's
    header."""
    return _unwrap(wrapper).time()

def framedata(buf, offset, wrapper):
    """framedata(buffer, offset, header) -> frame-data

    Extract the actual MP3-frame data from the MP3-frame starting at
    offset in buffer."""

    header = _unwrap(wrapper)
    start = header.length(include_side_info = False)
    end = MPEGFrame._calculate_length(header)
    return buf[offset + start : offset + end]

def framelen(wrapper):
    """framelen(header) -> length

    Calculate the length of an MP3-frame; both header and data."""
    return MPEGFrame._calculate_length(_unwrap(wrapper))

def frames(f):
    """frames(file) -> (header, frame) generator

    Extract all MP3-frames from a file-like object, returning them as
    (header, data) tuples, where header is as returned by frameheader
    and data is the entire MP3-frame data (including header).

    This is (unlike all other MP3 readers and players I know of) a
    strict MP3-reader; if there are any errors or bogus data in the file
    MP3Error is raised. The only accomodation made for non-MP3 data is
    ID3/APE tags and RIFF frames, which it will skip."""

    reader = Reader(f)
    for frame in reader.frames(skip_invalid_data = False, emit_meta_frames = False, \
        emit_riff_frames=False):
        yield _HeaderWrapper(frame.header), frame

def good_data(f):
    """good_data(file) -> good-data-buffer generator

    Extract all MP3-frames and ID3-tags from a file-like object,
    yielding their raw data buffers one at a time."""

    reader = Reader(f)
    for frame in reader.frames(skip_invalid_data = True, emit_meta_frames = True, \
        emit_riff_frames = False):
        yield frame