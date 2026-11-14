"""Wire format shared by the UDP client and server."""

import struct

# Versioned magic, message type, session ID, and sequence in network byte order.
HEADER = struct.Struct("!4sBQI")
MAGIC = b"ACN1"
DATA = 1
ACK = 2
MAX_PAYLOAD = 1000
PORT = 12000


def encode(kind, session, sequence, payload=b""):
    return HEADER.pack(MAGIC, kind, session, sequence) + payload


def decode(raw):
    if not HEADER.size <= len(raw) <= HEADER.size + MAX_PAYLOAD:
        return None
    magic, kind, session, sequence = HEADER.unpack_from(raw)
    if magic != MAGIC or sequence == 0 or kind not in (DATA, ACK):
        return None
    payload = raw[HEADER.size:]
    if (kind == DATA and not payload) or (kind == ACK and payload):
        return None
    return kind, session, sequence, payload
