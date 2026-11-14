#!/usr/bin/env python3
"""OS-Ken OpenFlow 1.3 controller for monitored shortest-path forwarding."""

import os
from itertools import permutations

from os_ken.base import app_manager
from os_ken.controller import ofp_event
from os_ken.controller.handler import CONFIG_DISPATCHER, DEAD_DISPATCHER, MAIN_DISPATCHER, set_ev_cls
from os_ken.ofproto import ofproto_v1_3

from topology_config import HOSTS, LINKS, configured_graph, find_path


class AdaptiveController(app_manager.OSKenApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.mode = os.environ.get("ACN_ROUTE_MODE", "adaptive")
        if self.mode not in {"adaptive", "static"}:
            raise ValueError("ACN_ROUTE_MODE must be adaptive or static")
        self.datapaths = {}
        self.ports = {}
        self.initialized = set()
        self.installed = {}
        self.last_paths = {}
        self.last_links = {}
        self.configured = configured_graph()
        self.logger.info("Controller mode=%s", self.mode)

    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def switch_features(self, event):
        dp = event.msg.datapath
        if dp.id not in self.configured:
            return
        self.datapaths[dp.id] = dp
        self.ports[dp.id] = {}
        self.initialized.discard(dp.id)
        self.installed.pop(dp.id, None)
        self.logger.info("CONNECTED s%d OpenFlow 1.3", dp.id)
        dp.send_msg(dp.ofproto_parser.OFPFlowMod(
            datapath=dp, table_id=0, command=dp.ofproto.OFPFC_DELETE,
            out_port=dp.ofproto.OFPP_ANY, out_group=dp.ofproto.OFPG_ANY,
            match=dp.ofproto_parser.OFPMatch()))
        dp.send_msg(dp.ofproto_parser.OFPPortDescStatsRequest(dp, 0))

    @set_ev_cls(ofp_event.EventOFPPortDescStatsReply, [CONFIG_DISPATCHER, MAIN_DISPATCHER])
    def port_description(self, event):
        dp = event.msg.datapath
        if self.datapaths.get(dp.id) is not dp:
            return
        for port in event.msg.body:
            self.ports[dp.id][port.port_no] = self.port_up(dp, port)
        if not event.msg.flags & dp.ofproto.OFPMPF_REPLY_MORE:
            self.initialized.add(dp.id)
            self.refresh_routes()

    @set_ev_cls(ofp_event.EventOFPPortStatus, [CONFIG_DISPATCHER, MAIN_DISPATCHER])
    def port_status(self, event):
        msg = event.msg
        dp = msg.datapath
        if self.datapaths.get(dp.id) is not dp:
            return
        self.ports[dp.id][msg.desc.port_no] = (
            msg.reason != dp.ofproto.OFPPR_DELETE and self.port_up(dp, msg.desc))
        if dp.id in self.initialized:
            self.refresh_routes()

    @set_ev_cls(ofp_event.EventOFPStateChange, DEAD_DISPATCHER)
    def disconnect(self, event):
        dp = event.datapath
        if self.datapaths.get(dp.id) is dp:
            del self.datapaths[dp.id]
            self.ports.pop(dp.id, None)
            self.initialized.discard(dp.id)
            self.installed.pop(dp.id, None)
            self.logger.info("DISCONNECTED s%d", dp.id)
            self.refresh_routes()

    @staticmethod
    def port_up(dp, port):
        return not (port.config & dp.ofproto.OFPPC_PORT_DOWN or
                    port.state & (dp.ofproto.OFPPS_LINK_DOWN | dp.ofproto.OFPPS_BLOCKED))

    def is_up(self, switch, port):
        return switch in self.initialized and self.ports[switch].get(port, False)

    def refresh_routes(self):
        if not self.initialized:
            return
        live = {switch: {} for switch in self.initialized}
        for a, a_port, b, b_port in LINKS:
            if a not in self.initialized or b not in self.initialized:
                continue
            up = self.is_up(a, a_port) and self.is_up(b, b_port)
            if self.last_links.get((a, b)) != up:
                self.logger.info("LINK s%d-s%d %s", a, b, "UP" if up else "DOWN")
                self.last_links[a, b] = up
            if up:
                live[a][b], live[b][a] = a_port, b_port

        graph = live if self.mode == "adaptive" else self.configured
        paths, rules = {}, {}
        for src, dst in permutations(HOSTS, 2):
            src_ip, src_switch, src_port = HOSTS[src]
            dst_ip, dst_switch, dst_port = HOSTS[dst]
            path = ()
            if self.is_up(src_switch, src_port) and self.is_up(dst_switch, dst_port):
                path = find_path(graph, src_switch, dst_switch)
            paths[src, dst] = path
            # Queue downstream entries before changing the ingress switch.
            for i in reversed(range(len(path))):
                switch = path[i]
                in_port = graph[switch][path[i - 1]] if i else src_port
                out_port = graph[switch][path[i + 1]] if i + 1 < len(path) else dst_port
                rules[switch, src_ip, dst_ip, in_port] = out_port

        for key, out_port in rules.items():
            switch, *match = key
            flows = self.installed.setdefault(switch, {})
            if flows.get(tuple(match)) != out_port:
                self.flow(self.datapaths[switch], match, out_port)
                flows[tuple(match)] = out_port
        for switch, flows in self.installed.items():
            for match in list(flows):
                if (switch, *match) not in rules:
                    self.flow(self.datapaths[switch], match, None)
                    del flows[match]

        for pair, path in paths.items():
            if self.last_paths.get(pair) != path:
                pretty = " -> ".join(f"s{switch}" for switch in path) or "UNREACHABLE"
                self.logger.info("ROUTE %s -> %s: %s", *pair, pretty)
        self.last_paths = paths

    @staticmethod
    def flow(dp, fields, out_port):
        src, dst, in_port = fields
        parser, ofp = dp.ofproto_parser, dp.ofproto
        match = parser.OFPMatch(in_port=in_port, eth_type=0x0800, ipv4_src=src, ipv4_dst=dst)
        if out_port is None:
            flow = parser.OFPFlowMod(datapath=dp, table_id=0, command=ofp.OFPFC_DELETE_STRICT,
                                     priority=100, out_port=ofp.OFPP_ANY,
                                     out_group=ofp.OFPG_ANY, match=match)
        else:
            actions = [parser.OFPActionOutput(out_port)]
            instructions = [parser.OFPInstructionActions(ofp.OFPIT_APPLY_ACTIONS, actions)]
            flow = parser.OFPFlowMod(datapath=dp, table_id=0, priority=100,
                                     match=match, instructions=instructions)
        dp.send_msg(flow)

    @set_ev_cls(ofp_event.EventOFPErrorMsg, [CONFIG_DISPATCHER, MAIN_DISPATCHER])
    def switch_error(self, event):
        msg = event.msg
        self.logger.error("OpenFlow error s%d: type=%s code=%s", msg.datapath.id, msg.type, msg.code)
