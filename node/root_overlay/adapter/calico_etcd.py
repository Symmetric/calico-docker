# Copyright (c) 2015 Metaswitch Networks
# All Rights Reserved.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.
"""
etcd operations for the /calico namespace.  Provides high level functions for adding and removing
workloads on a node.
"""
import socket
import etcd
from collections import namedtuple
import json
import sys
import os
import logging
import logging.handlers
from netaddr import IPAddress, AddrFormatError

_log = logging.getLogger(__name__)

Endpoint = namedtuple("Endpoint", ["id", "addrs", "mac", "state"])

HOST_PATH = "/calico/host/%(hostname)s/"
CONTAINER_PATH = "/calico/host/%(hostname)s/workload/docker/%(container_id)s/"
ENDPOINT_PATH = "/calico/host/%(hostname)s/workload/docker/%(container_id)s/" + \
                "endpoint/%(endpoint_id)s/"
GROUPS_PATH = "/calico/network/group/"
GROUP_MEMBER_PATH = "/calico/network/group/%(group_id)s/member"
ENDPOINTS_PATH = "/calico/host/%(hostname)s/workload/docker/%(container_id)s/endpoint/"

hostname = socket.gethostname()

ENV_ETCD = "ETCD_AUTHORITY"
"""The environment variable that locates etcd service."""


def setup_logging(logfile):
    _log.setLevel(logging.DEBUG)
    formatter = logging.Formatter('%(asctime)s [%(levelname)s] %(name)s %(lineno)d: %(message)s')
    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(logging.INFO)
    handler.setFormatter(formatter)
    _log.addHandler(handler)
    handler = logging.handlers.TimedRotatingFileHandler(logfile,
                                                        when='D',
                                                        backupCount=10)
    handler.setLevel(logging.DEBUG)
    handler.setFormatter(formatter)
    _log.addHandler(handler)


class CalicoEtcdClient(object):
    """
    An Etcd Client that exposes Calico specific operations on the etcd database.
    """

    def __init__(self):
        etcd_authority = os.getenv(ENV_ETCD, None)
        if not etcd_authority:
            self.client = etcd.Client()
        else:
            # TODO: Error handling
            (host, port) = etcd_authority.split(":", 1)
            self.client = etcd.Client(host=host, port=int(port))

    def create_container(self, hostname, container_id, endpoint):
        """
        Set up a container in the /calico/ namespace.  This function assumes 1
        container, with 1 endpoint.

        :param hostname: The hostname for the Docker hosting this container.
        :param container_id: The Docker container ID.
        :param endpoint: The Endpoint to add to the container.
        :return: Nothing
        """

        endpoint_path = ENDPOINT_PATH % {"hostname": hostname,
                                         "container_id": container_id,
                                         "endpoint_id": endpoint.id}

        _log.info("Creating endpoint at %s", endpoint_path)
        try:
            self.client.write(endpoint_path + "addrs", json.dumps(endpoint.addrs))
            self.client.write(endpoint_path + "mac", endpoint.mac)
            self.client.write(endpoint_path + "state", endpoint.state)
        except etcd.EtcdException as e:
            _log.exception("Hit Exception %s writing to etcd.", e)
            pass

    def get_default_next_hops(self, hostname):
        """
        Get the next hop IP addresses for default routes on the given host.

        :param hostname: The hostname for which to get default route next hops.
        :return: Dict of {ip_version: IPAddress}
        """

        host_path = HOST_PATH % {"hostname": hostname}
        ipv4 = self.client.read(host_path + "bird_ip").value
        ipv6 = self.client.read(host_path + "bird6_ip").value

        next_hops = {}

        # The IP addresses read from etcd could be blank. Only store them if
        # they can be parsed by IPAddress
        try:
            next_hops[4] = IPAddress(ipv4)
        except AddrFormatError:
            pass

        try:
            next_hops[6] = IPAddress(ipv6)
        except AddrFormatError:
            pass

        _log.info(next_hops)
        return next_hops

    def add_container_to_group(self, container_id, group_name):
        group_id = self.get_group_id(group_name)
        ep_id = self.get_ep_id_from_cont(container_id)
        group_path = GROUP_MEMBER_PATH % {"group_id": group_id}
        _log.info("Adding endpoint %s to group %s", ep_id, group_path)

        try:
            self.client.write(group_path + ep_id, "")
        except etcd.EtcdException as e:
            _log.exception("Hit Exception %s writing to etcd.", e)
            pass

    # TODO: We should import this directly from datastore.py
    # We can do that once we do away with the master Docker container.
    def get_group_id(self, name_to_find):
        """
        Get the UUID of the named group.  If multiple groups have the same name, the first matching
        one will be returned.
        :param name_to_find:
        :return: string UUID for the group, or None if the name was not found.
        """
        for group_id, name in self.get_groups().iteritems():
            if name_to_find == name:
                return group_id
        return None

    def get_groups(self):
        """
        Get the all configured groups.
        :return: a dict of group_id => name
        """
        groups = {}
        try:
            etcd_groups = self.client.read(GROUPS_PATH, recursive=True,).leaves
            for child in etcd_groups:
                packed = child.key.split("/")
                if len(packed) > 4:
                    (_, _, _, _, group_id, final_key) = packed[0:6]
                    if final_key == "name":
                        groups[group_id] = child.value
        except KeyError:
            # Means the GROUPS_PATH was not set up.  So, group does not exist.
            pass
        return groups

    def get_ep_id_from_cont(self, container_id):
        """
        Get a single endpoint ID from a container ID.

        :param container_id: The Docker container ID.
        :return: Endpoint ID as a string.
        """
        ep_path = ENDPOINTS_PATH % {"hostname": hostname,
                                    "container_id": container_id}
        try:
            endpoints = self.etcd_client.read(ep_path).leaves
        except KeyError:
            # Re-raise with better message
            raise KeyError("Container with ID %s was not found." % container_id)

        # Get the first endpoint & ID
        endpoint = endpoints.next()
        (_, _, _, _, _, _, _, _, endpoint_id) = endpoint.key.split("/", 8)
        return endpoint_id

