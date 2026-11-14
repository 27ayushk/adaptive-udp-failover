# Adaptive Communication Network with Failure Recovery

This extends the Part 1 Python UDP application and Mininet/Open vSwitch
triangle. `hA` (`10.0.0.1`) and `hC` (`10.0.0.3`) send concurrently to
`hB` (`10.0.0.2`) on UDP port 12000. The normal path is `s1 -> s3`.
When that link goes down, the controller can use `s1 -> s2 -> s3`.

## Run on Ubuntu in VirtualBox

Check for the tools already used in Part 1:

```bash
command -v mn ovs-vsctl ovs-ofctl
command -v osken-manager
```

If Open vSwitch is installed but stopped, run `sudo systemctl start openvswitch-switch`.
If Mininet or OVS is missing, install the Ubuntu `mininet` and
`openvswitch-switch` packages. Keep the working OS-Ken installation from
Part 1. If OS-Ken was installed in a virtual environment, activate that
environment first or pass the full path to `osken-manager`. For a fresh
OS-Ken setup, install `os-ken` in a Python virtual environment. The demo runs
a virtual environment's controller through its adjacent `bin/python3`, so a
copied `osken-manager` script's original interpreter path is ignored.

In the extracted project directory, run:

```bash
sudo python3 run_demo.py --controller "$(command -v osken-manager)"
```

The runner starts and stops the controller, Mininet, and the UDP processes.
It verifies initial connectivity with `pingall`. It then runs an adaptive
scenario and a static baseline under the same traffic and link-down schedule.
Both scenarios use the same UDP timeout and retry settings. The alternate
link is kept available, and the original link is restored before each
scenario finishes. Use `--port 16653` if OpenFlow port 6653 is already in use.

## Read the output

The terminal prints controller connectivity, the active path, link changes,
and per-client results. `results/comparison.csv` contains the measurements.
The controller log and three sets of OpenFlow flow dumps record forwarding
before, during, and after the outage. The per-client CSV files record each
logical message, its confirmation latency, and the number of attempts.

The demo passes if both adaptive clients receive ACKs while the primary link
is down, neither static client receives ACKs in that interval, and every
client receives ACKs again after restoration. `loss_percent` measures messages
that exhausted their retries. `first_link_down_ack_ms` measures the time from
completion of the link-down operation to the first ACK for a message started
during the outage. It includes the client's send interval. Confirmation latency
includes time spent waiting for retries.

## Start the components manually

Terminal 1 starts the controller:

```bash
PYTHONPATH="$PWD" osken-manager --ofp-listen-host 127.0.0.1 --ofp-tcp-listen-port 6653 ryu_controller.py
```

Terminal 2 starts Mininet:

```bash
sudo python3 mininet_topology.py
```

At the `mininet>` prompt:

```text
net
pingall
hB python3 -u udp_server.py >/tmp/acn-server.log 2>&1 &
hA python3 -u udp_client.py --count 50 >/tmp/acn-client.log 2>&1 &
link s1 s3 down
sh tail /tmp/acn-client.log
link s1 s3 up
exit
```

## Design notes

Mininet fixes the switch port assignments and configures host ARP entries at
startup. The controller uses OpenFlow 1.3 port descriptions and port-status
notifications to track the configured links. It calculates a shortest path and
programs forward and reverse flows, including one rule for each hop. Static
mode observes the same link events and keeps the configured shortest path.

Each UDP datagram carries a message type, session ID, and sequence number. The
client waits for a matching ACK and retries a missing ACK up to its attempt
limit. The server keeps a bounded cache of recently delivered messages and
ACKs duplicates without delivering the same recent message again. Start the
server with `--drop-first-ack` to demonstrate that behavior independently.

The controller uses a fixed inventory of lab links and hosts. It detects port
state changes and switch disconnects. Silent packet loss with every OpenFlow
port still up is handled by UDP retries within the retry budget.
