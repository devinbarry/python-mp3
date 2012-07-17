#
# _bitpack.py -- Fast if limited bit packing and unpacking routines
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

import math
import collections

_BITPACK_TYPES = {
    'i' : int,
    'b' : bool
}

class error(Exception):
    pass

class invalid_input_error(error):
    pass

class formatstr(object):
    format = None
    length = None

    def __init__(self, fmt):
        if isinstance(fmt, str):
            fmt = fmt.split(',')
        elif not isinstance(fmt, collections.Iterable):
            raise error('Format needs to be string or Iterable')

        self.format = list(self._parse(fmt))
        self.length = reduce(self._sum, self.format, 0)
        self.length = int(math.ceil(self.length / 8.0))

    def __iter__(self):
        for t in self.format:
            yield t

    @staticmethod
    def _sum(acc, (_, length, _1)):
        return acc + length

    @classmethod
    def _parse(cls, fmt):
        for chunk in fmt:
            default = None
            if '=' in chunk:
                chunk, default = chunk.rsplit('=', 1)
                default = default.startswith('0x') and \
                    int(default, 16) or int(default)
            
            if chunk == 'b':
                type, length = 'b', 1
            else:
                type, length = chunk.split(':', 1)

                if not length.isdigit():
                    raise error('Length has to be a base-10 integer')

                length = int(length)

            if not type in _BITPACK_TYPES:
                raise error('Unsupported type: %s' % type)

            yield _BITPACK_TYPES[type], length, default

def bitpack_into(fmt, buffer, offset, *values):
    fmt = isinstance(fmt, formatstr) and fmt or formatstr(fmt)

    if fmt.length > len(buffer) - offset:
        raise error('bitpack_into requires a buffer of at least %d bytes' % fmt.length)

    i = bit_offset = 0
    for type, length, _ in fmt:
        bitmask = pow(2, length) - 1
        value = type(values[i]) & bitmask

        while length > 0:
            # number of bits of the current value in this byte
            num_bits = min(8 - bit_offset, length)
            # number of bits of the current value remaining
            num_remaining_bits = max(0, length - num_bits)
            # number of bits of the current byte remaining empty
            num_empty_bits = 8 - bit_offset - num_bits

            # create bitmask for the leftmost remaining bits
            bitmask = pow(2, num_bits) - 1 << num_remaining_bits
            # get bits and right align them
            bits = (value & bitmask) >> num_remaining_bits
            # align bits within current byte
            bits <<= num_empty_bits

            # mask already packed bits on the left
            bitmask = (pow(2, bit_offset) - 1) << 8 - bit_offset
            # mask 'unused' bits on the right, if any
            bitmask |= (pow(2, num_empty_bits)) - 1

            # write value
            buffer[offset] = (buffer[offset] & bitmask) | bits

            bit_offset = (bit_offset + num_bits) % 8
            if bit_offset == 0: offset += 1
            length -= num_bits

        i += 1

#@profile
def bitunpack_from(fmt, buffer, offset = 0):
    fmt = isinstance(fmt, formatstr) and fmt or formatstr(fmt)

    if fmt.length > len(buffer) - offset:
        raise error('bitunpack_from requires a buffer of at least %d bytes' % fmt.length)

    values = []
    i = bit_offset = 0
    for type, length, default in fmt:
        value = 0
        while length > 0:
            # number of bits of the current value in this byte
            num_bits = min(8 - bit_offset, length)
            # number of bits of the current value remaining
            num_remaining_bits = max(0, length - num_bits)
            # number of bits of the current byte remaining empty
            num_empty_bits = 8 - bit_offset - num_bits

            # create bitmask for bytes interesting to us
            bitmask = pow(2, num_bits) - 1 << num_empty_bits
            # read bits and right justify
            bits = (buffer[offset] & bitmask) >> num_empty_bits

            # merge bits and already read value
            value |= bits << num_remaining_bits

            bit_offset = (bit_offset + num_bits) % 8
            if bit_offset == 0: offset += 1
            length -= num_bits

        i += 1
        value = type(value)
        
        if not default is None and value != default:
            raise invalid_input_error('Parsed value does not match expected default')
        
        values.append(value)

    return values