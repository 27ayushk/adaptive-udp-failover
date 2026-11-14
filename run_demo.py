#!/usr/bin/env python3
"""Compare measured UDP delivery under adaptive and static OpenFlow routing."""

import argparse
import csv
import os
from pathlib import Path
import platform
import shutil
import socket
import statistics
import subprocess
import sys
import time
from contextlib import ExitStack

from mininet.log import setLogLevel

from mininet_topology import create_network
from topology_config import PRIMARY

ROOT = Path(__file__).resolve().parent
CLIENTS = ("hA", "hC")
COUNT = 30
INTERVAL = 0.35
FAIL_AFTER = 2.4
OUTAGE = 4.2


def wait_for(check, label, seconds=12):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if check():
            return
        time.sleep(0.05)
    raise RuntimeError(f"Timed out waiting for {label}")


def stop(process):
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


def flow_dump():
    return "\n".join(subprocess.check_output(
        ["ovs-ofctl", "-O", "OpenFlow13", "dump-flows", switch], text=True, timeout=5)
        for switch in ("s1", "s2", "s3"))


def resolve_controller(command):
    candidate = shutil.which(command)
    script = Path(candidate or command).absolute()
    if script.is_file() and (script.parent.parent / "pyvenv.cfg").is_file():
        python = script.with_name("python3")
        if python.is_file() and os.access(python, os.X_OK):
            # A copied console script can still point to its original venv in the shebang.
            return [str(python), str(script)]
        raise RuntimeError(f"The controller's virtual environment has no executable {python}. "
                           "Recreate the OS-Ken virtual environment on this machine.")
    if candidate is None:
        raise RuntimeError("OS-Ken not found. Pass --controller /path/to/osken-manager")
    return [candidate]


def run_scenario(mode, manager, port, output):
    print(f"\n=== {mode.upper()} ROUTING ===", flush=True)
    with ExitStack() as cleanup:
        controller_path = output / f"{mode}-controller.log"
        controller_log = cleanup.enter_context(controller_path.open("w"))
        env = dict(os.environ, ACN_ROUTE_MODE=mode, PYTHONPATH=str(ROOT), PYTHONUNBUFFERED="1")
        try:
            controller = subprocess.Popen([*manager, "--ofp-listen-host", "127.0.0.1",
                                           "--ofp-tcp-listen-port", str(port), str(ROOT / "ryu_controller.py")],
                                          stdout=controller_log, stderr=subprocess.STDOUT, env=env, cwd=ROOT)
        except OSError as error:
            raise RuntimeError(f"Cannot start OS-Ken: {error}") from error
        cleanup.callback(stop, controller)

        def listening():
            if controller.poll() is not None:
                raise RuntimeError(f"Controller exited. See {controller_path}")
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                    return True
            except OSError:
                return False

        wait_for(listening, "OS-Ken")
        net = create_network(port)
        cleanup.callback(net.stop)
        net.start()
        if not net.waitConnected(timeout=10):
            raise RuntimeError("Open vSwitch did not connect to the controller")
        print("OpenFlow 1.3 connected s1, s2, s3", flush=True)
        origin, destination = (f"s{switch}" for switch in PRIMARY)
        print(f"Primary {origin} -> {destination} | Alternate s1 -> s2 -> s3", flush=True)
        pair = "ROUTE hA -> hB: s1 -> s3"
        wait_for(lambda: pair in controller_path.read_text(errors="replace"), "primary route")
        # OpenFlow updates can reach different switches at slightly different times.
        h_a, h_b = net.get("hA", "hB")
        wait_for(lambda: h_a.popen(["ping", "-c", "1", "-W", "1", h_b.IP()],
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).wait() == 0,
                 "working primary path")
        if net.pingAll(timeout="1") != 0:
            raise RuntimeError("pingall detected an initial connectivity problem")
        (output / f"{mode}-flows-before.txt").write_text(flow_dump(), encoding="utf-8")

        server_path = output / f"{mode}-server.log"
        server_log = cleanup.enter_context(server_path.open("w"))
        server = h_b.popen([sys.executable, str(ROOT / "udp_server.py")],
                          stdout=server_log, stderr=subprocess.STDOUT)
        cleanup.callback(stop, server)
        wait_for(lambda: "READY" in server_path.read_text(errors="replace"), "UDP server")
        clients = []
        for host in CLIENTS:
            client_log = cleanup.enter_context((output / f"{mode}-{host}.log").open("w"))
            client = net.get(host).popen(
                [sys.executable, str(ROOT / "udp_client.py"), "--count", str(COUNT),
                 "--interval", str(INTERVAL), "--csv", str(output / f"{mode}-{host}.csv")],
                stdout=client_log, stderr=subprocess.STDOUT)
            cleanup.callback(stop, client)
            clients.append(client)
        wait_for(lambda: all("seq=01 " in (output / f"{mode}-{host}.log").read_text(errors="replace")
                             for host in CLIENTS), "initial UDP exchanges")
        time.sleep(FAIL_AFTER)

        down = time.monotonic()
        net.configLinkStatus(origin, destination, "down")
        down_done = time.monotonic()
        print(f"LINK {origin}-{destination} DOWN while both clients are sending", flush=True)
        check = "ROUTE hA -> hB: s1 -> s2 -> s3" if mode == "adaptive" else f"LINK {origin}-{destination} DOWN"
        wait_for(lambda: check in controller_path.read_text(errors="replace"), "controller link event", 3)
        if mode == "adaptive":
            print("Controller selected s1 -> s2 -> s3", flush=True)
        (output / f"{mode}-flows-down.txt").write_text(flow_dump(), encoding="utf-8")
        time.sleep(max(0, down + OUTAGE - time.monotonic()))

        up = time.monotonic()
        net.configLinkStatus(origin, destination, "up")
        up_done = time.monotonic()
        print(f"LINK {origin}-{destination} UP", flush=True)
        if mode == "adaptive":
            wait_for(lambda: controller_path.read_text(errors="replace").count(pair) >= 2,
                     "controller return to the primary route", 3)
        for client in clients:
            if client.wait(timeout=15) != 0:
                raise RuntimeError(f"UDP client failed. See the {mode} client logs.")
        (output / f"{mode}-flows-restored.txt").write_text(flow_dump(), encoding="utf-8")

        return [summarize(mode, host, output, down_done, up, up_done) for host in CLIENTS]


def summarize(mode, host, output, down_done, up, up_done):
    with (output / f"{mode}-{host}.csv").open(newline="", encoding="utf-8") as source:
        rows = list(csv.DictReader(source))
    during = [row for row in rows if down_done <= float(row["sent_monotonic"]) < up]
    during_acks = [float(row["ack_monotonic"]) for row in during
                   if row["ack_monotonic"] and float(row["ack_monotonic"]) < up]
    acks = [row for row in rows if row["ack_monotonic"]]
    up_acks = [row for row in acks if float(row["sent_monotonic"]) >= up_done]
    report = {"mode": mode, "client": host, "sent": len(rows), "acked": len(acks),
              "loss_percent": round(100 * (len(rows) - len(acks)) / len(rows), 2),
              "retransmissions": sum(int(row["attempts"]) - 1 for row in rows),
              "mean_confirmation_ms": round(statistics.mean(float(row["latency_ms"]) for row in acks), 2)
              if acks else "N/A",
              "link_down_sent": len(during), "link_down_acked": len(during_acks),
              "first_link_down_ack_ms": round((min(during_acks) - down_done) * 1000, 2)
              if during_acks else "N/A", "post_restore_acked": len(up_acks)}
    print(f"{host} -> hB | ACKs {report['acked']}/{report['sent']} | loss {report['loss_percent']:.1f}% "
          f"| link down {len(during_acks)}/{len(during)} | retries {report['retransmissions']} "
          f"| mean confirmation {report['mean_confirmation_ms']} ms "
          f"| first ACK with link down {report['first_link_down_ack_ms']} ms", flush=True)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--controller", default="osken-manager")
    parser.add_argument("--port", default=6653, type=int)
    parser.add_argument("--out", type=Path, default=ROOT / "results")
    options = parser.parse_args()
    if os.geteuid() != 0:
        parser.error("run as root so Mininet can create network namespaces")
    try:
        manager = resolve_controller(options.controller)
    except RuntimeError as error:
        parser.error(str(error))
    try:
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", options.port))
    except OSError:
        parser.error(f"OpenFlow port {options.port} is busy. Stop the other controller or use --port.")
    subprocess.run(["ovs-vsctl", "show"], check=True, stdout=subprocess.DEVNULL, timeout=5)
    output = options.out.resolve()
    output.mkdir(parents=True, exist_ok=True)
    print(f"Host {platform.platform()} | Python {platform.python_version()}", flush=True)
    reports = [report for mode in ("adaptive", "static")
               for report in run_scenario(mode, manager, options.port, output)]
    with (output / "comparison.csv").open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=list(reports[0]))
        writer.writeheader()
        writer.writerows(reports)
    passed = all(r["link_down_sent"] > 0 and r["post_restore_acked"] > 0 and
                 (r["link_down_acked"] > 0 if r["mode"] == "adaptive" else r["link_down_acked"] == 0)
                 for r in reports)
    print(f"{'PASS' if passed else 'FAIL'} | Failover comparison | Results in {output}", flush=True)
    return 0 if passed else 1


if __name__ == "__main__":
    setLogLevel("info")
    sys.exit(main())
