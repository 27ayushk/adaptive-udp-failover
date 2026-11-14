# Adaptive Communication Network with Failure Recovery

This guide explains what changed after Part 1 and how to reproduce the UDP failover demo on Ubuntu in Oracle VirtualBox. The UDP client, UDP server, Mininet topology, and OS-Ken controller are Python, matching the Part 1 project.

## 1. What we built

Two Mininet hosts send sequenced UDP messages to one server. The controller monitors the inter-switch ports and changes the OpenFlow rules when a link goes down. The receiver acknowledges each valid message, and the sender retries an unacknowledged message. The experiment runs the same workload with adaptive and static routing so you can see whether the application keeps receiving acknowledgements while the primary link is down.

```text
hA  10.0.0.1 ─┐              s2
              ├── s1 ────────/  \──────── s3 ── hB  10.0.0.2
hC  10.0.0.3 ─┘    \_____________________/
                     primary s1–s3 link
```

- `hA` and `hC` are independent UDP clients. `hB` listens on UDP port `12000`.
- `s1 -> s3` is the normal path. `s1 -> s2 -> s3` is the alternate. ACKs traverse the reverse path.
- OS-Ken programs the Open vSwitch instances using OpenFlow 1.3. Its control connection in the demo listens on Ubuntu loopback port `6653`.

## 2. Prepare Ubuntu

1. Boot Ubuntu in Oracle VirtualBox. Copy `cn-mini-project.zip` into the guest and extract it. Use the guest Files app to copy the ZIP from a VirtualBox shared folder or mounted disc. Keep the extracted project in your Ubuntu home directory so it is writable.
2. Check that the system Python can import Mininet and that OVS tools are available.

   ```bash
   python3 -c 'from mininet.net import Mininet; print("Mininet import OK")'
   command -v mn ovs-vsctl ovs-ofctl
   ```

3. If those tools are missing, install them, then check the Open vSwitch service.

   ```bash
   sudo apt update
   sudo apt install mininet openvswitch-switch python3-venv unzip
   sudo systemctl start openvswitch-switch
   sudo ovs-vsctl show
   ```

4. Use an OS-Ken environment already built on your Ubuntu system if you have one. To set up a fresh one, run these commands as your Ubuntu user.

   ```bash
   python3 -m venv "$HOME/acn-osken"
   "$HOME/acn-osken/bin/python3" -m pip install os-ken
   "$HOME/acn-osken/bin/osken-manager" --help
   ```

   If your instructor or team supplied a known-working OS-Ken version, use it in the `pip install` command. Mininet runs with the system Python, and the controller runs with its own environment.

## 3. Run the full experiment

Open Terminal in the extracted project directory and run the following. If you already have an OS-Ken environment, replace `CONTROLLER` with the full path to its `osken-manager` file.

```bash
CONTROLLER="$HOME/acn-osken/bin/osken-manager"
OUT=$(mktemp -d "$HOME/acn-results.XXXXXX")
set -o pipefail
sudo python3 run_demo.py --controller "$CONTROLLER" --out "$OUT" 2>&1 | tee "$OUT/terminal.txt"
printf 'Results saved in %s\n' "$OUT"
```

The runner starts the controller, starts the three switches and hosts, waits for forwarding, and runs `pingall`. Then it starts the UDP server and both clients, takes `s1-s3` down for about 4.2 seconds while both clients are sending, and brings it up again. It repeats the procedure with static routing and stops the processes it started.

The automated result reads `PASS` when both adaptive clients receive ACKs while the link is down, both static clients receive zero ACKs during the outage, and every client receives ACKs after restoration. The terminal and CSV show the measured values on your VM. Keep the raw logs from your run for the report.

### Rerun from the original mounted disc

On the project VM, `ACN_DEMO` contains the earlier runner. Run the block below to reproduce the successful launch without changing the copied Part 1 environment. The temporary launcher calls the Part 1 environment's Python explicitly.

```bash
(
  set -e -o pipefail
  venv="$HOME/CN_MiniProject_Part1/CN_MiniProject_Complete/.venv"
  project="/run/media/$USER/ACN_DEMO"
  "$venv/bin/python3" "$venv/bin/osken-manager" --help >/dev/null
  launcher_dir=$(mktemp -d)
  printf '#!/bin/sh\nexec "%s/bin/python3" "%s/bin/osken-manager" "$@"\n' "$venv" "$venv" > "$launcher_dir/osken-manager"
  chmod +x "$launcher_dir/osken-manager"
  sudo python3 "$project/run_demo.py" --controller "$launcher_dir/osken-manager" --out /tmp/acn-results 2>&1 | tee "$HOME/acn-demo.txt"
)
```

## 4. Explain the code to the team

| File | Role |
| --- | --- |
| `topology_config.py` | Defines host addresses and switch port numbers. Breadth-first search selects the shortest available path by hop count. |
| `mininet_topology.py` | Builds the original switch triangle, adds a second client, and sets static ARP entries so the experiment can focus on IP forwarding. |
| `ryu_controller.py` | Runs as an OS-Ken app. Reads initial port descriptions and reacts to OpenFlow port-status and disconnect events. Installs and removes IPv4 forwarding rules for the affected paths. |
| `udp_protocol.py` | Defines the UDP header and validates incoming packets. The header carries a message type, session ID, and sequence number. |
| `udp_client.py` | Sends paced messages and waits for ACKs matching the current session and sequence. It retransmits up to the configured attempt limit and records the outcome. |
| `udp_server.py` | Receives datagrams from both clients in one socket loop. It ACKs a duplicate but does not deliver that recent message twice. |
| `run_demo.py` | Applies the same outage schedule to adaptive and static routing, captures controller logs and switch flow tables, and compares client metrics. |

The controller determines a link is usable only when both corresponding switch ports are up. In adaptive mode the graph contains usable links. It computes each direction separately and programs each hop. In static mode the graph retains the originally configured routes, so the controller observes the outage while traffic continues to use the failed primary path. The application’s retry settings stay the same in both modes.

The client times out after 100 ms per attempt by default and sends at most three attempts per message. If the server delivered a message but its ACK was lost, the client resends the same session and sequence. The server’s recent-message cache distinguishes duplicates by peer address, session, and sequence. The cache is bounded, and delivery is still subject to the client’s retry budget.

## 5. Read and share the results

| File | What to look for |
| --- | --- |
| `terminal.txt` | Initial connectivity, link down/up, alternate path selection, per-client summaries, and the final result. |
| `comparison.csv` | One row per client and routing mode. Compare ACK counts, loss, retransmissions, and confirmation latency. |
| `adaptive-controller.log` and `static-controller.log` | Switch connection messages, link events, and route decisions. |
| `*-flows-before.txt`, `*-flows-down.txt`, `*-flows-restored.txt` | OpenFlow rules for each switch. During adaptive failover, rules on `s2` forward traffic for the two clients. |
| `*-hA.csv` and `*-hC.csv` | Message-level timing and attempts for each client. |
| `*-server.log` | Server deliveries and duplicate datagrams. |

`loss_percent` is the percentage of logical messages that exhaust their attempts without an ACK. `mean_confirmation_ms` includes waits for retries. `first_link_down_ack_ms` measures the time from completion of the link-down command to the first ACK for a message started while the link was down. It includes the client’s send schedule and should not be reported as controller processing time. Run the demo several times if you need a repeatable estimate, and report the observed values from each run.

For the presentation, show the topology, a successful `pingall`, the adaptive controller’s route change, and the adaptive-versus-static comparison during the outage. Take the screenshot from the Ubuntu terminal after the final `PASS` line appears.

### Verified Ubuntu run

The project ran in Ubuntu inside Oracle VirtualBox using Python 3.14.4. All three OpenFlow switches connected. Both scenarios received 6 of 6 initial ping replies. During the adaptive run, the controller selected `s1 -> s2 -> s3` after `s1-s3` went down. The experiment ended with `PASS | Failover comparison`.

| Mode | Client | Total ACKs | ACKs while the link was down | Loss | Retransmissions | Mean confirmation | First ACK with link down |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Adaptive | `hA` → `hB` | 30/30 | 13/13 | 0.0% | 1 | 4.75 ms | 172.82 ms |
| Adaptive | `hC` → `hB` | 30/30 | 12/12 | 0.0% | 0 | 1.05 ms | 292.66 ms |
| Static | `hA` → `hB` | 24/30 | 0/7 | 20.0% | 13 | 11.93 ms | N/A |
| Static | `hC` → `hB` | 24/30 | 0/6 | 20.0% | 12 | 0.82 ms | N/A |

The archive includes the recovered `ubuntu-run.txt`, a CSV summary derived from its four client lines, and a terminal screenshot showing the static results and the final `PASS`. The Ubuntu run used the temporary launcher above and saved its raw logs, flow dumps, and `comparison.csv` under `/tmp/acn-results`. The updated ZIP incorporates the same interpreter selection, which was checked locally.

## 6. Troubleshoot the setup

- **`FileNotFoundError` naming `osken-manager`.** A console script copied from another machine can exist while its first line points to an interpreter that exists only on that machine. Inspect it with `head -n 1 "$CONTROLLER"`. This project’s updated runner uses the Python executable beside the script when it detects a virtual environment. If the interpreter is missing or a package import fails, recreate OS-Ken in a fresh Ubuntu virtual environment using section 2.
- **Mininet cannot find or connect to Open vSwitch.** Run `sudo ovs-vsctl show` to check the service. Start it with `sudo systemctl start openvswitch-switch` if stopped.
- **Port `6653` is busy.** Close your earlier controller session, or add `--port 16653` to the demo command. The runner uses the selected port for both OS-Ken and Mininet.
- **The project disc is missing under `/media`.** Run `lsblk -o NAME,LABEL,MOUNTPOINTS` and use the mount path reported for `ACN_DEMO`. On the Ubuntu VM used for this project, it was `/run/media/vboxuser/ACN_DEMO`.
- **Windows text will not paste.** Check VirtualBox **Devices → Shared Clipboard**. In Ubuntu Terminal, Ctrl+Shift+V pastes. On the project VM, `VBoxClient --clipboard` started the missing guest clipboard service. Copy fresh text after starting it.

The controller’s link inventory is configured for this topology. A port-state event or switch disconnect drives rerouting. When the switch ports remain up during silent packet loss, retransmission is the application’s recovery mechanism. No GitHub repository or submission is created by the demo.
