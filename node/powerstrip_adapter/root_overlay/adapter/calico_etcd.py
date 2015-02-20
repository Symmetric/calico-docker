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
import etcd
from collections import namedtuple
import json
import sys
import os
import logging
import logging.handlers

_log = logging.getLogger(__name__)

Endpoint = namedtuple("Endpoint", ["id", "addrs", "mac", "state"])

HOST_PATH = "/calico/host/%(hostname)s/"
CONTAINER_PATH = "/calico/host/%(hostname)s/workload/docker/%(container_id)s/"
ENDPOINT_PATH = "/calico/host/%(hostname)s/workload/docker/%(container_id)s/" + \
                "endpoint/%(endpoint_id)s/"

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
        Set up a container in the /calico/ namespace.  This function assumes 1 container, with 1
        endpoint.

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

    def get_container(self, hostname, container_id):
        """
        Get config for a container in the /calico/ namespace.  This function assumes 1 container,
        with 1 endpoint.

        :param hostname: The hostname for the Docker hosting this container.
        :param container_id: The Docker container ID.
        :param endpoint: The Endpoint to add to the container.
        :return: dict: A dictionary containing the container's config.
        """
        container_path = CONTAINER_PATH % {"hostname": hostname,
                                           "container_id": container_id}

        _log.info("Getting container config at %s", container_path)
        try:
            return self.client.get(container_path)
        except etcd.EtcdException as e:
            _log.exception("Hit Exception %s reading from etcd.", e)
            return None

    def get_container_address(self, hostname, container_id):
        """
        Get config for a container in the /calico/ namespace.  This function assumes 1 container,
        with 1 endpoint.

        :param hostname: The hostname for the Docker hosting this container.
        :param container_id: The Docker container ID.
        :return: dict: A dictionary containing the container's config.
        """
        return '1.2.3.4'
        # endpoints_path = (CONTAINER_PATH + 'endpoint/') % {"hostname": hostname,
        #                                                    "container_id": container_id}
        #
        # _log.info("Getting endpoint config at %s", endpoints_path)
        # try:
        #     # Note -- this library behaviour appears to be bugged.
        #     # .children returns the leaf nodes not the child nodes.
        #     # Ok for our purposes here though.
        #     endpoints_list = self.client.read(endpoints_path, recursive=True)
        #
        #     # Walk the nodes under /endpoint/*/, and look for the /endpoint/*/addrs key.
        #     for node in endpoints_list.children:
        #         _log.debug('Inspecting etcd node "node"')
        #         if node.key.endswith('/addrs'):
        #             address_str = node.value
        #             _log.debug('Found address: %s', address_str)
        #             break
        #     else:
        #         _log.warn('No address found for container %s', container_id)
        #         return
        #
        #     # Currently stashing a hokey JSON list in the 'addrs' etcd value.
        #     addrs = json.loads(address_str)
        #     # Just pull out the first address.
        #     address = addrs[0]['addr']
        #     _log.debug('Extracted address %s', address)
        #     return address
        # except etcd.EtcdException as e:
        #     _log.exception("Hit Exception %s reading from etcd.", e)
        #     pass
