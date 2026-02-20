#!/usr/bin/python3

# Copyright (C) 2025 Wolfgang Schildbach
#
# This program is free software: you can redistribute it and/or modify it under the terms of
# the GNU General Public License as published by the Free Software Foundation, either
# version 3 of the License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY;
# without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
# See the GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License along with this program.
# If not, see <https://www.gnu.org/licenses/>.

"""A daemon that listens to the docker socket, waiting for starting and stopping containers,
   and registering/deregistering .local domain names when a label mdns.publish=host.local
   is present """

__version__ = "1.2.1"

import os
import logging
from urllib.error import URLError
import signal
import datetime
import re
from dataclasses import dataclass

import netifaces
import zeroconf
import docker

from utils import adapter_ips
from utils import well_known_port_name, IgnoredError, FatalError

logger = logging.getLogger("docker-mdns-publisher")

@dataclass
class Configuration():
    """data structure to configure LocalHostWatcher"""
    def __init__(self):
        """read environment"""

        # The adapter(s) to listen on. If empty, will listen on all of them
        # we will listen and publish on all ip adresses of these adapters
        self.adapters = os.environ.get("ADAPTERS")
        if self.adapters is not None:
            self.adapters = self.adapters.split(',')

        # standard TTL is an hour
        self.publish_ttl = int(os.environ.get("TTL","3600"))

        # for now, hardcoded to IPv4 only
        self.ip_version = zeroconf.IPVersion.V4Only

        # The networks that are excluded from publishing
        self.excluded_nets = os.environ.get("EXCLUDED_NETS","")

        # These are the standard python log levels
        self.log_level = os.environ.get("LOG_LEVEL", "INFO")
        if self.log_level=="TRACE":
            logging.getLogger("zeroconf").setLevel(logging.DEBUG)
            self.log_level = "DEBUG"

        logging.basicConfig(level=self.log_level)

class LocalHostWatcher():
    """watch the docker socket for starting and dieing containers.
    Publish and unpublish mDNS records."""

    # ContextManager entry
    def __enter__(self):
        if self.config.ip_version !=  zeroconf.IPVersion.V4Only:
            raise NotImplementedError(f"IP_VERSION {self.config.ip_version} not supported")

        # if no adapters were configured, use all interfaces
        use_adapters = self.config.adapters or netifaces.interfaces()
        if not self.config.adapters:
            logger.warning("publishing on all interfaces: %s", use_adapters)

        # determine all adresses from the listed adapters.
        # filter against exclusion list (to disallow docker networks, for example)
        # check if all interface names actually exist
        for a in use_adapters:
            try:
                netifaces.ifaddresses(a)
            except ValueError as error:
                raise FatalError(f'invalid adapter/interface name "{a}": {error}') from error

            self.interfaces = adapter_ips(use_adapters, self.config.excluded_nets)
            logger.debug("publishing on interfaces IPs: %s", self.interfaces)

            self.zeroconf = zeroconf.Zeroconf(
                ip_version=self.config.ip_version, interfaces=self.interfaces
            )

        return self

    def __exit__(self, exc_type, exc_value, traceback):
        # Handle exceptions (if any)
        if exc_type:
            logger.debug("A %s occurred: %s",exc_type,exc_value)

        if hasattr(self,"zeroconf"):
            logger.debug("deregistering all registered hostnames")
            self.zeroconf.close()
            del self.zeroconf # not strictly necessary but safe

        return True  # Suppress exceptions

    def __init__(self,dockerclient):
        """set up the mdns registry"""

        # set our configuration from environment variables
        self.config = Configuration()

        self.dockerclient = dockerclient # so we know what to listen for
        self.interfaces = None # the interfaces that we advertise on
        self.zeroconf = None # the zeroconf instance
        self.info_store = {} # stores info structures, indexed by container id

    def __del__(self):
        pass

    def mkinfo(self,cname,port,service_type=None,props=None):
        """fill out the zeroconf ServiceInfo structure"""

        props = props or {}

        def is_valid_hostname(hostname):
            """determine if a hostname is valid.

            Originally found at https://stackoverflow.com/a/43211062, slightly modified.
            Licensed under [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/)
            (c) by [Alexx Roche](https://stackoverflow.com/users/1153645/alexx-roche)
            """

            if len(hostname) > 255:
                return False
            hostname = hostname.rstrip(".")
            allowed = re.compile(r"(?!-)[A-Z\d\-\_]{1,63}(?<!-)$", re.IGNORECASE)
            return all(allowed.match(x) for x in hostname.split("."))

        # the FQDN needs to end with a dot. Supply one to be user friendly
        if not cname.endswith('.'):
            cname += '.'

        # convert unicode hostname to punycode (python 3 )
        servername = cname.encode("idna").decode()

        if not is_valid_hostname(servername):
            raise IgnoredError(f"invalid server name {cname}")

        if not cname.endswith(".local."):
            raise IgnoredError("only .local domain is supported")

        cname = cname.removesuffix(".local.")

        if service_type is None:
            try:
                service_type = well_known_port_name[port]
            except KeyError as e:
                raise IgnoredError(f"port {port} is non standard. Supply a service type.") from e

        return zeroconf.ServiceInfo(
            f"{service_type}.local.", # fully qualified service type
            f"{cname}.{service_type}.local.", # fully qualified service type name
            addresses=self.interfaces,
            port=port,
            host_ttl=self.config.publish_ttl,
            server = servername,
            properties=props
        )

    def publish(self,cname,port,servicetype=None,props=None):
        """ publish the given record """

        logger.info("publishing %s:%d",cname,port)

        try:
            info = self.mkinfo(cname,port,servicetype,props=props)
            self.zeroconf.register_service(info, allow_name_change=False)
        except zeroconf.Error as error:
            raise IgnoredError(error,cname) from error

        return info

    def unpublish(self,info):
        """ unpublish the given record """

        logger.info("unpublishing %s:%d",info.name,info.port)
        self.zeroconf.unregister_service(info)

    def process_event(self,event):
        """when start/stop events are received, process the container that triggered the event """
        if event['Type'] == 'container' and event['Action'] in ('start','die'):
            container_id = event['Actor']['ID']
            try:
                container = self.dockerclient.containers.get(container_id)
                self.process_container(container_id,container,event['Action'])
            except URLError as error:
                # in some cases, containers may have already gone away when we process the event.
                # consider this harmless and ignore the error
                logger.warning("%s",error)
            except IgnoredError as error:
                logger.error(error)

    def process_container(self,container_id,container,action):
        """Run when a container triggered start/stop event.
             Checks whether the container has a label "mdns.publish" and if so, either
             registers or deregisters it"""

        def make_dict(s):
            """transform mdns.txt string into a dict"""
            a = []
            for t in s.split(','):
                if t.strip():
                    if '=' in t:
                        a.append( tuple(t.split('=')) )
                    else:
                        a.append( (t,'') )
            return dict(a)

        # parse host/service instance name
        hosts = container.labels.get("mdns.publish")

        # retrieve servicetype, if provided
        service_type = container.labels.get("mdns.servicetype")

        # parse txt records
        txt = make_dict(container.labels.get("mdns.txt",""))
        if self.config.log_level=="DEBUG":
            txt["container_id"]=container_id
            txt["publish_date"]=datetime.datetime.utcnow().strftime('%Y-%m-%d %H:%M %Z')

        if hosts is not None:
            for cname in hosts.split(','):
                # detect if specific port is being advertised
                port = 80
                if ":" in cname:
                    cname,port = cname.split(':')
                    port=int(port)

                # either register or deregister the name
                if action == 'start':
                    if container_id in self.info_store:
                        raise IgnoredError(f"trying to register more than one service ({cname}) \
                                                             for container {container_id}")
                    self.info_store[container_id] = self.publish(
                        cname,port,service_type,props=txt
                    )

                elif action == 'die':
                    try:
                        # if the service never was registered, this raises a KeyError
                        self.unpublish(self.info_store.pop(container_id))
                    except Exception:  # pylint: disable=broad-exception-caught
                        # catch any and all exceptions here -- we want to never fail
                        pass

    def run(self):
        """Initial scan of running containers and publish hostnames.
             Enumerate all running containers and register them"""

        # obtain the events stream before we iterate the containers, such that we are guaranteed
        # to get events that occur during the iteration
        events =  self.dockerclient.events(decode=True)

        logger.debug("registering running containers...")
        containers = self.dockerclient.containers.list(filters={"label":"mdns.publish"})
        for container in containers:
            self.process_container(container.id, container, "start")

        # now listen for Docker events and process them. We may double-process containers that
        # started during the initial iteration, but that is OK.
        logger.debug("waiting for container start/die...")
        for event in events:
            self.process_event(event)

def handle_signals(signum, frame): # pylint: disable=unused-argument
    """ does nothing except output a diagnostic message """

    signame = signal.Signals(signum).name
    logger.debug("Cleaning up on %s (%s)", signame, signum)

    raise KeyboardInterrupt()

if __name__ == '__main__':
    logger.info("docker-mdns-publisher daemon v%s starting.", __version__)

    try:
        with LocalHostWatcher(docker.from_env()) as LOCAL_WATCHER:
            signal.signal(signal.SIGTERM, handle_signals)
            signal.signal(signal.SIGINT,  handle_signals)
            LOCAL_WATCHER.run() # this will return only if interrupted
    except Exception as exception: # pylint: disable=broad-exception-caught
        # we don't really know which errors to expect here so we catch them all
        logger.critical("critical error '%s'",exception)

    logger.info("docker-mdns-publisher daemon terminating.")
