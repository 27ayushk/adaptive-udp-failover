#!/usr/bin/env python3
"""Receive concurrent UDP sessions and acknowledge each valid datagram."""

import argparse
from collections import OrderedDict
import socket

from udp_protocol import ACK, DATA, PORT, decode, encode

RECENT_LIMIT = 4096


def serve(address, port, drop_first_ack=False):
    # One receive loop serves all client endpoints without blocking on any peer.
    recent = OrderedDict()
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.bind((address, port))
        print(f"READY UDP server {address}:{sock.getsockname()[1]}", flush=True)
        while True:
            raw, sender = sock.recvfrom(65535)
            packet = decode(raw)
            if packet is None or packet[0] != DATA:
                continue
            _, session, sequence, payload = packet
            key = (sender, session, sequence)
            duplicate = key in recent
            recent[key] = None
            recent.move_to_end(key)
            if len(recent) > RECENT_LIMIT:
                recent.popitem(last=False)
            status = "DUPLICATE" if duplicate else "DELIVERED"
            print(f"{status} peer={sender[0]}:{sender[1]} "
                  f"session={session:016x} seq={sequence} bytes={len(payload)}", flush=True)
            if drop_first_ack and sequence == 1 and not duplicate:
                print("TEST: suppressed first ACK", flush=True)
                continue
            try:
                sock.sendto(encode(ACK, session, sequence), sender)
            except OSError as error:
                print(f"ACK send failed: {error}", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bind", default="10.0.0.2")
    parser.add_argument("--port", type=int, default=PORT)
    parser.add_argument("--drop-first-ack", action="store_true", help="exercise duplicate handling")
    options = parser.parse_args()
    try:
        serve(options.bind, options.port, options.drop_first_ack)
    except KeyboardInterrupt:
        pass
