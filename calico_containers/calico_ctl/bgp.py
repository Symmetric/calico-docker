# Copyright 2015 Metaswitch Networks
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""
Usage:
  calicoctl bgp peer add <PEER_IP> as <AS_NUM>
  calicoctl bgp peer remove <PEER_IP>
  calicoctl bgp peer show [--ipv4 | --ipv6]
  calicoctl bgp node-mesh [on|off]
  calicoctl bgp default-node-as [<AS_NUM>]


Description:
  Configure default global BGP settings for all nodes. Note: per-node settings
  will override these globals for that node.

Options:
 --ipv4    Show IPv4 information only.
 --ipv6    Show IPv6 information only.
"""
import sys
from utils import client
from pycalico.datastore_datatypes import BGPPeer
from netaddr import IPAddress
from utils import check_ip_version
from prettytable import PrettyTable
from utils import get_container_ipv_from_arguments


def bgp(arguments):
    """
    Main dispatcher for bgp commands. Calls the corresponding helper function.

    :param arguments: A dictionary of arguments already processed through
    this file's docstring with docopt
    :return: None
    """
    ip_version = get_container_ipv_from_arguments(arguments)
    if arguments.get("peer"):
        if arguments.get("add"):
            bgp_peer_add(arguments.get("<PEER_IP>"), ip_version,
                        arguments.get("<AS_NUM>"))
        elif arguments.get("remove"):
            bgp_peer_remove(arguments.get("<PEER_IP>"), ip_version)
        elif arguments.get("show"):
            if not ip_version:
                bgp_peer_show("v4")
                bgp_peer_show("v6")
            else:
                bgp_peer_show(ip_version)

    elif arguments.get("node-mesh"):
        if arguments.get("on") or arguments.get("off"):
            set_bgp_node_mesh(arguments.get("on"))
        else:
            show_bgp_node_mesh()
    elif arguments.get("default-node-as"):
        if arguments.get("<AS_NUM>"):
            set_default_node_as(arguments.get("<AS_NUM>"))
        else:
            show_default_node_as()


def bgp_peer_add(ip, version, as_num):
    """
    Add a new global BGP peer with the supplied IP address and AS Number.  All
    nodes will peer with this.

    :param ip: The address to add
    :param version: v4 or v6
    :param as_num: The peer AS Number.
    :return: None
    """
    address = check_ip_version(ip, version, IPAddress)
    peer = BGPPeer(address, as_num)
    client.add_bgp_peer(version, peer)


def bgp_peer_remove(ip, version):
    """
    Remove a global BGP peer.

    :param ip: The address to use.
    :param version: v4 or v6
    :return: None
    """
    address = check_ip_version(ip, version, IPAddress)
    try:
        client.remove_bgp_peer(version, address)
    except KeyError:
        print "%s is not a globally configured peer." % address
        sys.exit(1)
    else:
        print "BGP peer removed from global configuration"


def bgp_peer_show(version):
    """
    Print a list of the global BGP Peers.
    """
    assert version in ("v4", "v6")
    peers = client.get_bgp_peers(version)
    if peers:
        heading = "Global IP%s BGP Peer" % version
        x = PrettyTable([heading, "AS Num"], sortby=heading)
        for peer in peers:
            x.add_row([peer.ip, peer.as_num])
        x.align = "l"
        print x.get_string(sortby=heading)
    else:
        print "No global IP%s BGP Peers defined.\n" % version


def set_default_node_as(as_num):
    """
    Set the default node BGP AS Number.

    :param as_num:  The default AS number
    :return: None.
    """
    client.set_default_node_as(as_num)


def show_default_node_as():
    """
    Display the default node BGP AS Number.

    :return: None.
    """
    value = client.get_default_node_as()
    print value


def show_bgp_node_mesh():
    """
    Display the BGP node mesh setting.

    :return: None.
    """
    value = client.get_bgp_node_mesh()
    print "on" if value else "off"


def set_bgp_node_mesh(enable):
    """
    Set the BGP node mesh setting.

    :param enable:  (Boolean) Whether to enable or disable the node-to-node
    mesh.
    :return: None.
    """
    client.set_bgp_node_mesh(enable)
