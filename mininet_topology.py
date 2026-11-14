#!/usr/bin/env python3
"""Run the Part 1 Mininet triangle with two UDP clients and one server."""

import argparse

from mininet.cli import CLI
from mininet.log import setLogLevel
from mininet.net import Mininet
from mininet.node import OVSSwitch, RemoteController
from mininet.topo import Topo

from topology_config import HOSTS, LINKS, SWITCHES


class RecoveryTopo(Topo):
    def build(self):
        for switch in SWITCHES:
            self.addSwitch(f"s{switch}", dpid=f"{switch:016x}", protocols="OpenFlow13",
                           failMode="secure", inband=False)
        for host, (ip, switch, port) in HOSTS.items():
            self.addHost(host, ip=f"{ip}/24")
            self.addLink(host, f"s{switch}", port2=port)
        for a, a_port, b, b_port in LINKS:
            self.addLink(f"s{a}", f"s{b}", port1=a_port, port2=b_port)


def create_network(port):
    net = Mininet(topo=RecoveryTopo(), switch=OVSSwitch, controller=None,
                  autoSetMacs=True, autoStaticArp=True)
    net.addController("c0", controller=RemoteController, ip="127.0.0.1", port=port)
    return net


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=6653)
    options = parser.parse_args()
    setLogLevel("info")
    network = create_network(options.port)
    try:
        network.start()
        if not network.waitConnected(timeout=10):
            raise SystemExit("Switches did not connect to the controller.")
        CLI(network)
    finally:
        network.stop()
