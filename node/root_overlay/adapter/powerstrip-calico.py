# Copyright (c) 2015 Metaswitch Networks
# All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may
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
import re

from twisted.internet import reactor
from twisted.web import server, resource
import json
import logging
import logging.handlers
import sys
from docker import Client
import netns
import calico_etcd
import socket

_log = logging.getLogger(__name__)

ENV_IP = "CALICO_IP"
ENV_GROUP = "CALICO_GROUP"

hostname = socket.gethostname()

LISTEN_PORT = 2378


def setup_logging(logfile):
    _log.setLevel(logging.DEBUG)
    formatter = logging.Formatter(
        '%(asctime)s [%(levelname)s] %(filename)s.%(name)s %(lineno)d: '
        '%(message)s')
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

    # Propagate to loaded modules
    calico_etcd.setup_logging(logfile)
    netns.setup_logging(logfile)


class AdapterResource(resource.Resource):
    isLeaf = True

    def __init__(self):
        resource.Resource.__init__(self)

        # Init a Docker client, to save having to do so every time a request comes in.
        self.docker = Client(base_url='unix://var/run/docker.sock')

        # Init an etcd client.
        self.etcd = calico_etcd.CalicoEtcdClient()

    def render_POST(self, request):
        """
        Handle a pre-hook.
        """
        request_content = json.loads(request.content.read())
        if request_content["Type"] == "pre-hook":
            return self._handle_pre_hook(request, request_content)
        elif request_content["Type"] == "post-hook":
            return self._handle_post_hook(request, request_content)
        else:
            raise Exception("unsupported hook type %s" %
                            (request_content["Type"],))

    def _handle_pre_hook(self, request, request_content):
        # Make sure we always have something to return.
        client_request = {}
        # noinspection PyBroadException
        # Exceptions hang the Reactor, so pokemon-catch them all here.
        try:
            _log.debug("Pre-hook request: %s", request_content)
            client_request = request_content["ClientRequest"]

            _client_request_net_none(client_request)
        except BaseException:
            _log.exception('Unexpected error handling pre-hook.')
        finally:
            _log.debug(
                'Returning output:\n%s',
                json.dumps(
                    {
                        "PowerstripProtocolVersion": 1,
                        "ModifiedClientRequest": client_request
                    },
                    indent=2))
            return json.dumps({"PowerstripProtocolVersion": 1,
                               "ModifiedClientRequest": client_request})

    def _handle_post_hook(self, request, request_content):
        # Make sure we always have something to return
        # noinspection PyBroadException
        # Exceptions hang the Reactor, so pokemon-catch them all here.
        try:
            _log.debug("Post-hook response: %s", request_content)
            # Extract ip, group, master, docker_options
            client_request = request_content["ClientRequest"]
            server_response = request_content["ServerResponse"]
            request_uri = client_request['Request']
            # Strip left-most '/' (if present) to avoid always having an empty
            # request_path[0].
            request_path = request_uri.lstrip('/').split('/')

            if re.match('v[.\d]+', request_path[0]):
                # Got an explicitly versioned path, so remove the version so
                # that it looks the same as an unversioned path, e.g.
                # /v1.16/containers/json => /containers/json
                request_path.pop(0)

            if (len(request_path) == 3 and
                        request_path[0] == u'containers'):
                # Got /containers/*/*
                container_id = request_path[1]

                if request_path[2] == u'start':
                    # Got /containers/id/start
                    _log.debug('Intercepted container start request')
                    self._install_endpoint(container_id)
                elif request_path[2] == u'json':
                    # Got /containers/*/json
                    _log.debug('Intercepted container json request')
                    self._update_container_info(container_id, server_response)
                else:
                    _log.debug('Unrecognized path: %s', request_path)
            else:
                _log.debug('Unrecognized path of length %d: %s',
                           len(request_path), request_path)
        except BaseException:
            _log.exception('Unexpected error handling post-hook.')
        finally:
            try:
                output = json.dumps({
                    "PowerstripProtocolVersion": 1,
                    "ModifiedServerResponse": server_response
                })
                _log.debug('Returning output:\n%s', output)
            except:
                _log.exception('Error in finally')
            return output

    def _install_endpoint(self, container_id):
        """
        Install a Calico endpoint (veth) in the container referenced in the
        client request object.
        :param str container_id: The UUID of the container to install an
                                 endpoint in.
        :returns: None
        """

        try:
            # Get the container ID
            # TODO better URI parsing
            # /*/containers/*/start
            _log.debug("cid %s", container_id)

            # Grab the running pid from Docker
            cont = self.docker.inspect_container(container_id)
            _log.debug("Container info: %s", cont)
            pid = cont["State"]["Pid"]
            _log.debug('pid=%s', pid)

            # Attempt to parse out environment variables
            env_list = cont["Config"]["Env"]
            env_dict = env_to_dictionary(env_list)
            ip = env_dict[ENV_IP]
            _log.debug('Installing IP %s for container with PID %s', ip, pid)

            # TODO: process groups
            group = env_dict.get(ENV_GROUP, None)

            endpoint = netns.set_up_endpoint(ip=ip, cpid=pid)

            self.etcd.create_container(hostname=hostname,
                                       container_id=container_id,
                                       endpoint=endpoint)
            _log.info("Finished network for container %s, IP=%s",
                      container_id, ip)

        except KeyError as e:
            _log.warning("Key error %s, container_id: %s", e, container_id)

        return

    def _update_container_info(self, container_id, server_response):
        """
        Update the response for a */container/*/json (docker inspect) request.

        Since we've patched the docker networking using --net=none,
        docker inspect calls will not return any IP information. This is
        required for some orchestrators (such as Kubernetes).

        Insert the IP for this container into the config  dict.

        :param str container_id: The UUID of the container to update.
        :param dict server_response: The response from the Docker API, to be
                                     be updated.
        """
        _log.debug('Getting container config from etcd')

        try:
            address = self.etcd.get_container_address(
                hostname=hostname,
                container_id=container_id)
        except KeyError:
            _log.debug('No workload found for container %s, '
                       'returning request unmodified.', container_id)
            return

        _log.debug('Got address: %s', address)
        _log.debug('Pre-load body:\n%s', server_response["Body"])

        body = json.loads(server_response["Body"])
        body['NetworkSettings']['IPAddress'] = address
        server_response['Body'] = json.dumps(body, separators=(',', ':'))

        _log.debug('Post-load body:\n%s', server_response["Body"])


def _client_request_net_none(client_request):
    """
    Modify the client_request in place to set net=None Docker option.

    :param client_request: Powerstrip ClientRequest object as dictionary from
                           JSON
    :return: None
    """
    try:
        # Body is passed as a string, so deserialize it to JSON.
        body = json.loads(client_request["Body"])

        try:
            host_config = body["HostConfig"]
        except KeyError:
            # Request body didn't contain a HostConfig; insert one.
            host_config = body["HostConfig"] = {
                "HostConfig": {
                    "Binds": None,
                    "ContainerIDFile": "",
                    "LxcConf": [],
                    "Privileged": False,
                    "PortBindings": {},
                    "Links": None,
                    "PublishAllPorts": False,
                    "Dns": None,
                    "DnsSearch": None,
                    "ExtraHosts": None,
                    "VolumesFrom": None,
                    "Devices": [],
                    "NetworkMode": "none",
                    "IpcMode": "",
                    "CapAdd": None,
                    "CapDrop": None,
                    "RestartPolicy": {"Name": "", "MaximumRetryCount": 0},
                    "SecurityOpt": None,
                }
            }
        _log.debug("Original NetworkMode: %s",
                   host_config.get("NetworkMode", "<unset>"))
        host_config["NetworkMode"] = "none"
        _log.debug('New HostConfig: %s', host_config)

        # Re-serialize the updated body.
        client_request["Body"] = json.dumps(body)
        _log.debug('New client_request: %s', client_request)
    except KeyError as e:
        _log.warning("Error setting net=none: %s, request was %s",
                     e, client_request)


def get_adapter():
    root = resource.Resource()
    root.putChild("calico-adapter", AdapterResource())
    site = server.Site(root)
    return site


def env_to_dictionary(env_list):
    """
    Parse the environment variables into a dictionary for easy access.
    :param env_list: list of strings in the form "var=value"
    :return: a dictionary {"var": "value"}
    """
    env_dict = {}
    for pair in env_list:
        (var, value) = pair.split("=", 1)
        env_dict[var] = value
    return env_dict


class CalicoException(Exception):
    pass


if __name__ == "__main__":
    setup_logging("/var/log/calico/powerstrip-calico.log")
    # Listen only on the loopback so we don't expose the adapter outside the
    # host.
    reactor.listenTCP(LISTEN_PORT, get_adapter(), interface="127.0.0.1")
    reactor.run()

