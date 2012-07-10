import math
import collections

_BITPACK_TYPES = {
    'i' : int,
    'b' : bool
}

class formatstr(object):
    format = None
    length = None

    def __init__(self, fmt):
        if isinstance(fmt, str):
            fmt = fmt.split(',')
        elif not isinstance(fmt, collections.Iterable):
            raise Exception('Format needs to be string or Iterable')

        self.format = list(self._parse(fmt))
        self.length = reduce(self._sum, self.format, 0)
        self.length = int(math.ceil(self.length / 8.0))

    def __iter__(self):
        for t in self.format:
            yield t

    @staticmethod
    def _sum(acc, (_, length)):
        return acc + length

    @classmethod
    def _parse(cls, fmt):
        for chunk in fmt:
            if chunk == 'b':
                type, length = 'b', 1
            else:
                type, length = chunk.split(':', 1)

                if not length.isdigit():
                    raise Exception('Length has to be a base-10 integer')

                length = int(length)

            if not type in _BITPACK_TYPES:
                raise Exception('Unsupported type: %s' % type)

            yield _BITPACK_TYPES[type], length

def bitpack_into(fmt, buffer, offset, *values):
    fmt = isinstance(fmt, formatstr) and fmt or formatstr(fmt)

    if len(buffer) < offset + fmt.length:
        raise Exception('Not enough space in buffer')

    i = bit_offset = 0
    for type, length in fmt:
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

@profile
def bitunpack_from(fmt, buffer, offset = 0):
    fmt = isinstance(fmt, formatstr) and fmt or formatstr(fmt)

    if len(buffer) < offset + fmt.length:
        raise Exception('Not enough space in buffer, need %d + %d have %d' % (offset, fmt.length, len(buffer)))

    values = []
    i = bit_offset = 0
    for type, length in fmt:
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
        values.append(type(value))

    return values