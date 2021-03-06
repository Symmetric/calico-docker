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
  calicoctl node [--ip=<IP>] [--ip6=<IP6>] [--node-image=<DOCKER_IMAGE_NAME>] [--as=<AS_NUM>] [--log-dir=<LOG_DIR>]
  calicoctl node stop [--force]
  calicoctl node bgp peer add <PEER_IP> as <AS_NUM>
  calicoctl node bgp peer remove <PEER_IP>
  calicoctl node bgp peer show [--ipv4 | --ipv6]

Description:
  Configure the main calico/node container as well as default BGP information
  for this node.

Options:
  --force                  Stop the node process even if it has active endpoints.
  --node-image=<DOCKER_IMAGE_NAME>    Docker image to use for Calico's per-node
                                      container [default: calico/node:latest]
  --log-dir=<LOG_DIR>      The directory for logs [default: /var/log/calico]
  --ip=<IP>                The local management address to use.
  --ip6=<IP6>              The local IPv6 management address to use.
  --as=<AS_NUM>            The default AS number for this node.
  --ipv4                   Show IPv4 information only.
  --ipv6                   Show IPv6 information only.
"""
import sys
import os
import sh
import docker
import netaddr
import socket

from pycalico.datastore_datatypes import IPPool
from utils import ORCHESTRATOR_ID
from utils import hostname
from utils import client
from utils import docker_client
from pycalico.datastore_datatypes import BGPPeer
from pycalico.datastore import (ETCD_AUTHORITY_ENV,
                                ETCD_AUTHORITY_DEFAULT)
from checksystem import check_system
from utils import check_ip_version
from netaddr import IPAddress
from prettytable import PrettyTable
from utils import get_container_ipv_from_arguments

DEFAULT_IPV4_POOL = IPPool("192.168.0.0/16")
DEFAULT_IPV6_POOL = IPPool("fd80:24e2:f998:72d6::/64")


def node(arguments):
    """
    Main dispatcher for node commands. Calls the corresponding helper function.

    :param arguments: A dictionary of arguments already processed through
    this file's docstring with docopt
    :return: None
    """
    if arguments.get("bgp"):
        if arguments.get("peer"):
            ip_version = get_container_ipv_from_arguments(arguments)
            if arguments.get("add"):
                node_bgppeer_add(arguments.get("<PEER_IP>"), ip_version,
                                 arguments.get("<AS_NUM>"))
            elif arguments.get("remove"):
                node_bgppeer_remove(arguments.get("<PEER_IP>"), ip_version)
            elif arguments.get("show"):
                if not ip_version:
                    node_bgppeer_show("v4")
                    node_bgppeer_show("v6")
                else:
                    node_bgppeer_show(ip_version)
    elif arguments.get("stop"):
        node_stop(arguments.get("--force"))
    else:
        node_start(ip=arguments.get("--ip"),
                   node_image=arguments['--node-image'],
                   log_dir=arguments.get("--log-dir"),
                   ip6=arguments.get("--ip6"),
                   as_num=arguments.get("--as"))


def node_start(node_image, log_dir, ip="", ip6="", as_num=None):
    """
    Create the calico-node container and establish Calico networking on this
    host.

    :param ip:  The IPv4 address of the host.
    :param node_image:  The calico-node image to use.
    :param ip6:  The IPv6 address of the host (or None if not configured)
    :param as_num:  The BGP AS Number to use for this node.  If not specified
    the global default value will be used.
    :return:  None.
    """
    # Ensure log directory exists
    if not os.path.exists(log_dir):
        os.makedirs(log_dir)

    # Print warnings for any known system issues before continuing
    check_system(fix=False, quit_if_error=False)

    # Get IP address of host, if none was specified
    if not ip:
        ips = get_host_ips(4)
        try:
            ip = ips.pop()
        except IndexError:
            print "Couldn't autodetect a management IP address. Please provide" \
                  " an IP by rerunning the command with the --ip=<IP_ADDRESS> flag."
            sys.exit(1)
        else:
            print "No IP provided. Using detected IP: %s" % ip

    # Verify that the chosen IP exists on the current host
    warn_if_unknown_ip(ip, ip6)

    # Warn if this hostname conflicts with an existing host
    warn_if_hostname_conflict(ip)

    # Set up etcd
    ipv4_pools = client.get_ip_pools("v4")
    ipv6_pools = client.get_ip_pools("v6")

    # Create default pools if required
    if not ipv4_pools:
        client.add_ip_pool("v4", DEFAULT_IPV4_POOL)
    if not ipv6_pools:
        client.add_ip_pool("v6", DEFAULT_IPV6_POOL)

    client.ensure_global_config()
    client.create_host(hostname, ip, ip6, as_num)

    try:
        docker_client.remove_container("calico-node", force=True)
    except docker.errors.APIError as err:
        if err.response.status_code != 404:
            raise

    etcd_authority = os.getenv(ETCD_AUTHORITY_ENV, ETCD_AUTHORITY_DEFAULT)
    etcd_authority_split = etcd_authority.split(':')
    if len(etcd_authority_split) != 2:
        print_paragraph("Invalid %s. Must take the form <address>:<port>. Value " \
              "provided is '%s'" % (ETCD_AUTHORITY_ENV, etcd_authority))
        sys.exit(1)

    etcd_authority_address = etcd_authority_split[0]
    etcd_authority_port = etcd_authority_split[1]

    # Always try to convert the address(hostname) to an IP. This is a noop if
    # the address is already an IP address.
    etcd_authority = '%s:%s' % (socket.gethostbyname(etcd_authority_address),
                                etcd_authority_port)

    environment = [
        "HOSTNAME=%s" % hostname,
        "IP=%s" % ip,
        "IP6=%s" % (ip6 or ""),
        "ETCD_AUTHORITY=%s" % etcd_authority,  # etcd host:port
        "FELIX_ETCDADDR=%s" % etcd_authority,  # etcd host:port
    ]

    binds = {
        "/proc":
            {
                "bind": "/proc_host",
                "ro": False
            },
        log_dir:
            {
                "bind": "/var/log/calico",
                "ro": False
            },
        "/usr/share/docker/plugins/": #TODO make this an optional node
        # parameter like log_dir
        #"/run/docker/plugins/":
            {
                "bind": "/usr/share/docker/plugins",
                "ro": False
            }
    }

    host_config = docker.utils.create_host_config(
        privileged=True,
        restart_policy={"Name": "Always"},
        network_mode="host",
        binds=binds)

    _find_or_pull_node_image(node_image, docker_client)
    container = docker_client.create_container(
        node_image,
        name="calico-node",
        detach=True,
        environment=environment,
        host_config=host_config,
        volumes=["/proc_host",
                 "/var/log/calico",
                 "/usr/share/docker/plugins"])
    cid = container["Id"]

    docker_client.start(container)

    print "Calico node is running with id: %s" % cid


def node_stop(force):
    if force or len(client.get_endpoints(hostname=hostname, orchestrator_id=ORCHESTRATOR_ID)) == 0:
        client.remove_host(hostname)
        try:
            docker_client.stop("calico-node")
        except docker.errors.APIError as err:
            if err.response.status_code != 404:
                raise

        print "Node stopped and all configuration removed"
    else:
        print "Current host has active endpoints so can't be stopped." + \
              " Force with --force"


def node_bgppeer_add(ip, version, as_num):
    """
    Add a new BGP peer with the supplied IP address and AS Number to this node.

    :param ip: The address to add
    :param version: v4 or v6
    :param as_num: The peer AS Number.
    :return: None
    """
    address = check_ip_version(ip, version, IPAddress)
    peer = BGPPeer(address, as_num)
    client.add_bgp_peer(version, peer, hostname=hostname)


def node_bgppeer_remove(ip, version):
    """
    Remove a global BGP peer from this node.

    :param ip: The address to use.
    :param version: v4 or v6
    :return: None
    """
    address = check_ip_version(ip, version, IPAddress)
    try:
        client.remove_bgp_peer(version, address, hostname=hostname)
    except KeyError:
        print "%s is not a configured peer for this node." % address
        sys.exit(1)
    else:
        print "BGP peer removed from node configuration"


def node_bgppeer_show(version):
    """
    Print a list of the BGP Peers for this node.
    """
    assert version in ("v4", "v6")
    peers = client.get_bgp_peers(version, hostname=hostname)
    if peers:
        heading = "Node specific IP%s BGP Peer" % version
        x = PrettyTable([heading, "AS Num"], sortby=heading)
        for peer in peers:
            x.add_row([peer.ip, peer.as_num])
        x.align = "l"
        print x.get_string(sortby=heading)
    else:
        print "No IP%s BGP Peers defined for this node.\n" % version


def get_host_ips(version):
    """
    Gets all IP addresses assigned to this host.

    :param version: Desired version of IP addresses. Can be 4 or 6
    :return: List of string representations of IP Addresses.
    """
    ip = sh.Command._create("ip")
    ip_addrs = []
    addrs_raw = ip("-o", "-%d" % version, "addr").stdout.strip().split("\n")
    for address_output in addrs_raw:
        # Each 'address_output' represents a line showing the interface ip
        values = address_output.split()
        # Ignore the loopback interface
        if 'lo' not in values:
            # Extract the IP, ensure its valid
            ip_addrs.append(str(netaddr.IPNetwork(values[3]).ip))
    return ip_addrs


def warn_if_unknown_ip(ip, ip6):
    """
    Prints a warning message if the IP addresses are not assigned to interfaces
    on the current host.

    :param ip: IPv4 address which should be present on the host.
    :param ip6: IPv6 address which should be present on the host.
    :return: None
    """
    if ip not in get_host_ips(4):
        print "WARNING: Could not confirm that the provided IPv4 address is assigned" \
              " to this host."

    if ip6 and ip6 not in get_host_ips(6):
        print "WARNING: Could not confirm that the provided IPv6 address is assigned" \
              " to this host."


def warn_if_hostname_conflict(ip):
    """
    Prints a warning message if it seems like an existing host is already running
    calico using this hostname.

    :param ip: User-provided IP address to start this node with.
    :return: Nothing
    """
    # If there's already a calico-node container on this host, they're probably
    # just re-running node to update one of the ip addresses, so skip..
    if len(docker_client.containers(filters={'name': 'calico-node'})) == 0:
        # Otherwise, check if another host with the same hostname
        # is already configured
        try:
            current_ipv4, _ = client.get_host_ips(hostname)
        except KeyError:
            # No other machine has registered configuration under this hostname.
            # This must be a new host with a unique hostname, which is the
            # expected behavior.
            pass
        else:
            if current_ipv4 != "" and current_ipv4 != ip:
                print "WARNING: Hostname '%s' is already in use with IP address " \
                      "%s. Calico requires each compute host to have a " \
                      "unique hostname. If this is your first time running " \
                      "'calicoctl node' on this host, ensure that " \
                      "another host is not already using the " \
                      "same hostname."  % (hostname, ip)


def _find_or_pull_node_image(image_name, client):
    """
    Check if Docker has a cached copy of an image, and if not, attempt to pull
    it.

    :param image_name: The full name of the image.
    :return: None.
    """
    try:
        _ = client.inspect_image(image_name)
    except docker.errors.APIError as err:
        if err.response.status_code == 404:
            # TODO: Display proper status bar
            print "Pulling Docker image %s" % image_name
            client.pull(image_name)
