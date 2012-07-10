#
# mp3.py -- MP3-frame meta-data parser
# Copyright (C) 2003-2004  Sune Kirkeby
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
from _bitpack import bitunpack_from, bitpack_into, formatstr as bitpack_formatstr
from _crc16 import crc16
import os
import math

class MP3Error(Exception):
    """I signal a generic error related to MP3-data."""
    pass
class MP3FrameHeaderError(MP3Error):
    """I signal that there was an error parsing the meta-data in an
    MP3-frame header."""
    pass

class _InvalidFrame(Exception):
    pass

class Channelmode:
    """Read-only convenience container for the different MPEG channel modes. See
    http://www.mp3-tech.org/programmer/frame_header.html for further information."""
    STEREO, JOINT_STEREO, DUAL_CHANNEL, MONO = range(4)

class _Bunch:
    def __init__(self, **kwds):
        self.__dict__.update(kwds)

def _unpack_from(fmt, buf, offset = 0):
    return struct.unpack_from(fmt, buffer(buf), offset)

def _pack_into(fmt, buf, offset, *vals):
    return struct.pack_into(fmt, buf, offset, *vals)

class Frame(object):
    _buffer = None
    length = None

    @property
    def bytes(self):
        return self._buffer

    def __str__(self):
        return str(self._buffer)

    def __eq__(self, other):
        if isinstance(other, Frame):
            return self._buffer == other._buffer
        else:
            return self._buffer == other

    def _frame_assembled(self):
        pass

    def append(self, view):
        self._buffer = bytearray(view[0:self.length])
        self._frame_assembled()

class MetaFrame(Frame):
    pass

class ID3Frame(MetaFrame):
    V1, V2 = range(2)

    version = None

    def __init__(self, buffer, fileobj = None, offset = 0, strict = False):
        if buffer.startswith('TAG', offset):
            self.version = self.V1
            self.length  = 128
        elif buffer.startswith('ID3', offset):
            self.version = self.V2

            if len(buffer) < 10:
                raise MP3Error, 'need at least 10 bytes of data'

            self.length = (buffer[offset + 6] << 21) + (buffer[offset + 7] << 14) + \
                (buffer[offset + 8] << 7) + (buffer[offset + 9]) + 10
        else:
            raise _InvalidFrame

class APEFrame(MetaFrame):
    V1, V2 = range(2)
    _HEADER_SIZE = 32

    version = None

    def __init__(self, buffer, fileobj = None, offset = 0, strict = False):
        if not buffer.startswith('APETAGEX', offset):
            raise _InvalidFrame

        version, length = _unpack_from('<II', buffer, offset + 8)
        if version == 2000: self.version = self.V2
        elif version == 1000: self.version = self.CV1

        self.length = length + self._HEADER_SIZE

class RIFFFrame(Frame):
    def __init__(self, buffer, fileobj = None, offset = 0, strict = False):
        if buffer.startswith('RIFF', offset) and buffer.startswith('WAVE', offset + 8):
            if fileobj: fileobj._has_riff_header = True
            self.length = 12

        elif fileobj is None or fileobj._has_riff_header:
            if buffer.startswith('data', offset):
                self.length = 8

            elif buffer.startswith('fmt ', offset):
                self.length, format = _unpack_from('<IH', buffer, offset + 4)
                self.length += 8

                if format != 0x55: # Not MPEG 1 Layer III data
                    raise MP3Error('encountered RIFF file with non MPEG 1 Layer III data, format = 0x%x' % format)

            elif buffer.startswith('fact', offset):
                self.length, = _unpack_from('<I', buffer, offset + 4)
                self.length += 8

            else:
                raise _InvalidFrame
        else:
            raise _InvalidFrame

class MPEGFrame(Frame):
    def __init__(self, buffer, fileobj = None, offset = 0, strict = False):
        try:
            header = Header(buffer, offset)
        except MP3FrameHeaderError, e:
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
        self._buffer[0:self.header.length()] = self.header.bytes()

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
    _MIN_HEADER_SIZE = 4 + 4

    def __init__(self, buffer, fileobj = None, offset = 0, strict = False):
        if fileobj and fileobj._has_xing_header == False:
            raise _InvalidFrame

        mpeg_length = super(XingFrame, self).\
            __init__(buffer, fileobj, offset=offset, strict=strict)

        if not mpeg_length:
            if fileobj: fileobj._has_xing_header = False
            raise _InvalidFrame

        header = self.header

        # Some implementations write the Xing Header at the wrong position if the frame
        # has CRC enabled. Check both places.
        offset += header.length(True)

        if buffer.startswith('Xing', offset) or buffer.startswith('Info', offset) or \
            (header.crc and (buffer.startswith('Xing', offset + 2) or \
            buffer.startswith('Info', offset + 2))):

            if fileobj: fileobj._has_xing_header = True

            self.has_vbr_quality, self.has_toc, self.has_total_size, self.has_total_frames = \
                [bool(buffer[offset+7] & 1 << 3 - i) for i in xrange(4)]

            length = header.length() + self._MIN_HEADER_SIZE
            if self.has_vbr_quality:  length += 4
            if self.has_toc:          length += 100
            if self.has_total_size:   length += 4
            if self.has_total_frames: length += 4

            self.xing_length = length
            self.length = mpeg_length

            assert(mpeg_length >= length)

        else:
            if fileobj: fileobj._has_xing_header = False
            raise _InvalidFrame

    def _frame_assembled(self):
        super(XingFrame, self)._frame_assembled()

        offset = self.header.length() + self._MIN_HEADER_SIZE

        if self.has_total_frames:
            self.total_frames, = _unpack_from('>I', buffer, offset)
            offset += 4

        if self.has_total_size:
            self.total_size, = _unpack_from('>I', buffer, offset)
            offset += 4

        if self.has_toc:
            self.toc = list(_unpack_from('>100B', buffer, offset))
            self.offset += 100

        if self.has_vbr_quality:
            self.vbr_quality, = _unpack_from('>I', buffer, offset)
            self.offset += 4

    def seekpoint(self, percent, file_size = None):
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
    _FRAME_TYPES = (XingFrame, MPEGFrame, RIFFFrame, ID3Frame, APEFrame)
    _MIN_FRAME_SIZE = 38

    _offset = 0
    _has_riff_header = False
    _has_xing_header = None

    _mpeg_version = None
    _mpeg_layer = None

    _blocksize = 8192

    def __init__(self, inobj):
        self._inobj = inobj

    def frames(self, ignore_invalid_data = True, emit_meta_frames = True, \
        emit_riff_frames = True):
        in_sync = True

        try:
            buffer = bytearray()
            buffer.extend(self._inobj.read(self._blocksize))

            while len(buffer) > 4: # We need at least 4 bytes for our shortest header
                # Try to parse a frame
                frame = None
                for frame_class in self._FRAME_TYPES:
                    try:
                        frame = frame_class(buffer, self, strict=not in_sync)
                        break
                    except _InvalidFrame:
                        pass

                if frame and not in_sync:
                    # Recover from lost sync
                    try:
                        self._fill_buffer(buffer, frame.length + 12)

                        # See if there is a consequent valid frame
                        for frame_class in self._FRAME_TYPES:
                            try:
                                frame_class(buffer, self, offset=frame.length, strict=True)
                                in_sync = True
                                break
                            except _InvalidFrame:
                                pass
    
                        frame = in_sync and frame or None
                    except EOFError:
                        # Not enough data left to check the next frame, accept it anyways
                        pass

                if frame:
                    self._fill_buffer(buffer, frame.length)

                    frame.append(memoryview(buffer))
                    del buffer[:frame.length]

                    if (isinstance(frame, MetaFrame) and emit_meta_frames) or \
                        (isinstance(frame, RIFFFrame) and emit_riff_frames) or \
                        isinstance(frame, MPEGFrame):
                        yield frame
                else:
                    in_sync = False

                    if not ignore_invalid_data:
                        raise MP3Error('encountered invalid data')

                    del buffer[:1]

                buffer.extend(self._inobj.read(self._blocksize))
        except EOFError:
            if not ignore_invalid_data:
                raise MP3Error('encountered invalid data')
        finally:
            del buffer

    def _fill_buffer(self, buffer, want_length):
        while len(buffer) < want_length:
            inbuf = self._inobj.read(self._blocksize)
            if len(inbuf) == 0: break
            buffer.extend(inbuf)

        if len(buffer) < want_length:
            raise EOFError

class Header(object):
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
        ('i:11', 'sync'),
        ('i:2',  'version'),
        ('i:2',  'layer'),
        ('b',    'crc'),
        ('i:4',  'bitrate'),
        ('i:2',  'samplingrate'),
        ('b',    'padding'),
        ('b',    'private'),
        ('i:2',  'channelmode'),
        ('i:2',  'modeextension'),
        ('b',    'copyright'),
        ('b',    'original'),
        ('i:2',  'emphasis')
    )

    _FORMAT, _FIELDS = zip(*_HEADER)
    _FORMAT = bitpack_formatstr(_FORMAT)

    _crc16 = None
    _side_info = None

    def __init__(self, buffer, offset = 0):
        if buffer is None:
            self.__dict__.update(dict.fromkeys(self._FIELDS))
        else:
            self.__dict__.update( \
                zip(self._FIELDS, bitunpack_from(self._FORMAT, buffer, offset)))

            for key in self._FIELDS:
                # This works as a basic validator
                getattr(self, key)

            self.update(buffer, offset)

    def update(self, buffer, offset = 0):
        # Read up to max(side_chan_info) + crc
        length = len(buffer)
        offset += 4 # Skip header

        if self.crc:
            if not self._crc16 and length >= offset + 2:
                self._crc16, = _unpack_from('>H', buffer, offset)

            offset += 2

        if not self._side_info and length >= offset + self.side_info_size():
            self._side_info = buffer[offset : offset + self.side_info_size()]


    def bytes(self, include_crc = True):
        buffer = bytearray(self._FORMAT.length)
        bitpack_into(self._FORMAT, buffer, 0, *[self.__dict__[k] for k in self._FIELDS])

        pos = len(buffer)
        buffer.extend(self._side_info)

        if include_crc and self.crc:
            crc = crc16(memoryview(buffer)[2:])
            buffer[pos:pos] = 2 # Inserts two bytes at pos
            _pack_into('>H', buffer, pos, crc)

        return buffer

    def side_info_size(self):
        return self._SIDE_INFO_SIZE[self.version > 1][self.channelmode == Channelmode.MONO]

    def length(self, include_crc = True, include_side_info = True):
        return 4 + include_crc * self.crc * 2 +  include_side_info * self.side_info_size()

    def valid(self):
        if not self.crc:
            return True

        return self._crc16 == self.calculate_crc()

    def calculate_crc(self):
        # TODO: Check for empty side info?
        # First two bytes of the header are skipped
        buffer = self.bytes(include_crc = False)
        return crc16(memoryview(buffer)[2:])

    def time(self):
        return (self.layer == 1) and (384.0 / 44100) or (1152.0 / 44100)

    @property
    def sync(self):
        sync = self.__dict__['sync']

        if sync != 0x7FF:
            raise MP3FrameHeaderError, 'frame sync not found'

        return sync

    @property
    def version(self):
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
        return not self.__dict__['crc']

    @crc.setter
    def crc(self, val):
        self.__dict__['crc'] = not val

    @property
    def bitrate(self):
        bitrate = self.__dict__['bitrate']

        if bitrate == 0xF or bitrate == 0x0:
            raise MP3FrameHeaderError, 'bad bitrate'

        return self._BITRATES[int(self.version)-1][self.layer-1][bitrate-1]

    @bitrate.setter
    def bitrate(self, value):
        try:
            self.__dict__['bitrate'] = \
                self._BITRATES[int(self.version)-1][self.layer-1].index(value) + 1
        except ValueError as e:
            raise MP3FrameHeaderError('invalid bitrate: %d' % value)

    @property
    def samplingrate(self):
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
        except ValueError as e:
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
    ID3 tags, which it will skip."""

    reader = Reader(f)
    for frame in reader.frames(ignore_invalid_data = False, emit_meta_frames = False):
        yield _HeaderWrapper(frame.header), frame

def good_data(f):
    """good_data(file) -> good-data-buffer generator

    Extract all MP3-frames and ID3-tags from a file-like object,
    yielding their raw data buffers one at a time."""

    reader = Reader(f)
    for frame in reader.frames(ignore_invalid_data = True, emit_meta_frames = True, \
        emit_riff_frames = False):
        yield frame