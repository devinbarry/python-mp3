#
# _buffer.py -- Zero copy buffer
# Copyright (C) 2012 Lorenz Bauer
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

import struct
import _bitpack

class Error(Exception):
    pass

class ZeroCopyBuffer(object):
    '''
    Implements a buffer object that facilitates zero copy design.
    '''

    def __init__(self, size, fileobj = None, _buffer = None):
        '''
        __init__(size) -> ZeroCopyBuffer object
        
        Initializes a buf of specified size.
        '''
        
        self._size = size
        self._buffer = _buffer or bytearray(size)
        self._fileobj = fileobj
        self._pos = 0
        self._len = 0
        
    def __len__(self):
        return self._len - self._pos
    
    def __getitem__(self, key):
        if isinstance(key, slice):
            raise TypeError('slicing is not supported')
        else:
            pos = key < 0 and max(self._pos, self._len + key) \
                or min(self._len, self._pos + key)
            return self._buffer[pos]
        
#    def __delitem__(self, key):
#        if isinstance(key, slice):
#            if key.start != 0 and not key.start is None:
#                raise ValueError('Can only delete starting at 0')
#            
#            self._pos = min(self._pos + key.stop, self._len)
#        elif key == 0:
#            self._pos = min(self._pos + 1, self._len)
#        else:
#            raise ValueError('Deleting is not supported for these arguments')
        
    def __eq__(self, other):
        if isinstance(other, ZeroCopyBuffer):
            return self.view() == other.view()
        else:
            return self.view() == other
        
    def bytes(self, *args):
        '''
        bytes(offset = 0, length = None) -> bytearray
        
        Returns a copy of length bytes from the specified offset and of specified length.
        Pass in None for length if you want all available data.
        '''
        return bytearray(self.view(*args))
    
    def view(self, offset = 0, length = None):
        '''
        view(offset = 0, length = None) -> memoryview object
        
        Returns a view of the buffer, from the specified offset and of specified length.
        Pass in None for length if you want all available data. Use this if you need direct
        access to the buffer's contents.
        '''
        offset = max(0, offset)
        length = length and self._pos + offset + length or self._len
        return memoryview(self._buffer)[self._pos + offset:length]
    
    def fill(self, fileobj = None, completely = False, at_least = None):
        '''
        fill(fileobj = None) -> nothing
        
        Tries to fill the buffer as much as possible from a file-like
        object. If fileobj is None, the default fileobj passed at construction
        time will be used. If completely is True and the fileobj is too short
        to completely fill the buffer, an EOFError will be raised. Additionally,
        if the buffer does not have at_least bytes, an EOFError will be raised, too.
        '''
        if not at_least is None and len(self) >= at_least:
            return
        
        self._shift_buffer()
        
        fileobj = fileobj and fileobj or self._fileobj
        if fileobj is None:
            raise TypeError('fileobj is not a valid file-like object')
        
        m = memoryview(self._buffer)[self._len:]
        length = fileobj.readinto(m)
        if length: self._len += length
        
        if completely and self._len != self._size:
            raise EOFError
        
        if not at_least is None and len(self) < at_least:
            raise EOFError
    
    def extend(self, buf):
        '''
        extend(buf) -> number of bytes copied
        
        Append string-like buf to the buffer. If buf is longer than space is available
        in the buffer only a part of buf will be copied.
        '''
        self._shift_buffer()
        length = min(len(buf), self._size - self._len)
        
        view = None
        if isinstance(buf, ZeroCopyBuffer):
            view = buf.view(0, length)
        else:
            view = memoryview(buf)[0:length]
        
        self._buffer[self._len:self._len + length] = view
        self._len += length
        
        return length
    
    def delete(self, length):
        self._pos = min(self._pos + length, self._len)
        
    def replace(self, src, offset = 0):
        if offset < 0:
            raise TypeError('offset must be positive')
        
        src_len = len(src)
        if src_len + offset > len(self):
            raise Error('src is too large for this buffer')
        
        if isinstance(src, ZeroCopyBuffer):
            src = src.view()
        
        self.view(offset, src_len)[:] = src
    
    def _struct_check_length(self, fmt, offset):
        if offset < 0:
            raise struct.error('can not (un)pack at a negative offset')
        
        fmt_size = struct.calcsize(fmt)
        if fmt_size > len(self) - offset:
            raise struct.error('(un)pack requires a buffer of at least %d bytes' % fmt_size)
    
    def pack(self, fmt, offset = 0, *vals):
        self._struct_check_length(fmt, offset)
        return struct.pack_into(fmt, self._buffer, self._pos + offset, *vals)
    
    def unpack(self, fmt, offset = 0):
        self._struct_check_length(fmt, offset)
        return struct.unpack_from(fmt, buffer(self._buffer), self._pos + offset)
    
    def _bitpack_check_length(self, fmt, offset):
        if offset < 0:
            raise _bitpack.error('can not bit(un)pack at a negative offset')
        
        if fmt.length > len(self) - offset:
            raise _bitpack.error('bit(un)pack requires a buffer of at least %d bytes' % fmt.length)
    
    def bitunpack(self, fmt, offset = 0):
        fmt = isinstance(fmt, _bitpack.formatstr) and fmt or _bitpack.formatstr(fmt)
        self._bitpack_check_length(fmt, offset)
        
        return _bitpack.bitunpack_from(fmt, self._buffer, self._pos + offset)
    
    def bitpack(self, fmt, offset = 0, *vals):
        fmt = isinstance(fmt, _bitpack.formatstr) and fmt or _bitpack.formatstr(fmt)
        self._bitpack_check_length(fmt, offset)
        
        return _bitpack.bitpack_into(fmt, self._buffer, self._pos + offset, *vals)
        
    def _shift_buffer(self):
        length = len(self)
        if length == 0:
            self._pos = 0
            self._len = 0
            return
        
        # We're already as far left in the buffer as were going to get
        if self._pos == 0:
            return
        
        source = self.view()
        memoryview(self._buffer)[0:length] = source
        self._pos = 0
        self._len = length
        
    def startswith(self, prefix, offset = 0):
        '''
        startswith(prefix, offset = 0) -> bool
        
        Tests if the buffer starts with the given prefix, starting at the specified
        offset.
        '''
        pos = min(self._len, self._pos + offset)
        return self._buffer.startswith(prefix, pos, self._len)
    