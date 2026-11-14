#!/usr/bin/env python3
"""Send a paced stream of UDP messages with ACKs and bounded retries."""

import argparse
import csv
import math
from pathlib import Path
import secrets
import socket
import statistics
import time

from udp_protocol import ACK, DATA, PORT, decode, encode


def send_message(sock, datagram, session, sequence, limit, timeout):
    for attempt in range(1, limit + 1):
        try:
            sock.send(datagram)
        except ConnectionRefusedError:
            # Connected UDP may report an ICMP error from a previous send.
            continue
        deadline = time.monotonic() + timeout
        while (remaining := deadline - time.monotonic()) > 0:
            sock.settimeout(remaining)
            try:
                response = decode(sock.recv(65535))
            except (TimeoutError, ConnectionRefusedError):
                break
            if response == (ACK, session, sequence, b""):
                return attempt, time.monotonic()
    return limit, None


def run(options):
    session = secrets.randbits(64)
    rows = []
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        # UDP connect limits received ACKs to the configured server endpoint.
        sock.connect((options.host, options.port))
        schedule_start = time.monotonic()
        print(f"READY UDP client session={session:016x}", flush=True)
        for sequence in range(1, options.count + 1):
            delay = schedule_start + (sequence - 1) * options.interval - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            payload = f"packet-{sequence}".encode()
            sent_at = time.monotonic()
            attempts, ack_at = send_message(sock, encode(DATA, session, sequence, payload),
                                            session, sequence, options.attempts, options.timeout)
            latency = (ack_at - sent_at) * 1000 if ack_at is not None else None
            rows.append((sequence, sent_at, ack_at, attempts, latency))
            outcome = f"ACK latency={latency:.2f} ms" if latency is not None else "TIMEOUT"
            print(f"seq={sequence:02d} attempts={attempts} {outcome}", flush=True)

    if options.csv:
        with options.csv.open("w", newline="", encoding="utf-8") as output:
            writer = csv.writer(output)
            writer.writerow(["sequence", "sent_monotonic", "ack_monotonic", "attempts", "latency_ms"])
            writer.writerows(rows)

    latencies = [row[4] for row in rows if row[4] is not None]
    delivered = len(latencies)
    mean = f"{statistics.mean(latencies):.2f}" if latencies else "N/A"
    print(f"SUMMARY messages={len(rows)} acknowledged={delivered} "
          f"loss={(len(rows)-delivered)/len(rows):.1%} attempts={sum(row[3] for row in rows)} "
          f"mean_confirmation_ms={mean}", flush=True)


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="10.0.0.2")
    parser.add_argument("--port", type=int, default=PORT)
    parser.add_argument("--count", type=int, default=30)
    parser.add_argument("--interval", type=float, default=0.35)
    parser.add_argument("--timeout", type=float, default=0.10)
    parser.add_argument("--attempts", type=int, default=3)
    parser.add_argument("--csv", type=Path)
    options = parser.parse_args()
    if not (1 <= options.port <= 65535 and 1 <= options.count <= 100000
            and 1 <= options.attempts <= 10 and math.isfinite(options.interval)
            and math.isfinite(options.timeout) and options.interval > 0 and options.timeout > 0):
        parser.error("use a valid port and positive, finite counts and timing values")
    return options


if __name__ == "__main__":
    run(parse_args())
