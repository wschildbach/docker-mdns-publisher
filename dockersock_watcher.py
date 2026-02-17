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

__version__ = "1.1.0"

import os
import re
import logging
from urllib.error import URLError
import signal

import netifaces
import zeroconf
import docker

from utils import adapter_ips

# standard TTL is an hour
PUBLISH_TTL = int(os.environ.get("TTL","3600"))
# These are the standard python log levels
LOGGING_LEVEL = os.environ.get("LOG_LEVEL", "INFO")
# get local domain from enviroment and escape all period characters
LOCAL_DOMAIN = re.sub(r'\.','\\.',os.environ.get("LOCAL_DOMAIN",".local"))
# for now, hardcoded to IPv4 only
IP_VERSION = zeroconf.IPVersion.V4Only
# The adapter(s) to listen on. If empty, will listen on all of them
# we will listen and publish on all ip adresses of these adapters
ADAPTERS = os.environ.get("ADAPTERS")
if ADAPTERS is not None:
    ADAPTERS=ADAPTERS.split(',')
# The networks that are excluded from publishing
EXCLUDED_NETS = os.environ.get("EXCLUDED_NETS","")

logger = logging.getLogger("docker-mdns-publisher")
logging.basicConfig(level=LOGGING_LEVEL)

if LOGGING_LEVEL=="TRACE":
    logging.getLogger("zeroconf").setLevel(logging.DEBUG)

class LocalHostWatcher():
    """watch the docker socket for starting and dieing containers.
    Publish and unpublish mDNS records."""

    # Set up compiler regexes to find sanitize labels
    hostnamerule = re.compile(r'^\s*[\w\-\.]+\s*$')
    localrule = re.compile(r'.+'+LOCAL_DOMAIN+r'\.?')

    # ContextManager entry
    def __enter__(self):
        if IP_VERSION !=  zeroconf.IPVersion.V4Only:
            raise NotImplementedError(f"IP_VERSION {IP_VERSION} not supported")

        # if no adapters were configured, use all interfaces
        if ADAPTERS:
            use_adapters = ADAPTERS
        else:
            use_adapters = netifaces.interfaces()
            logger.debug("publishing on all interfaces: %s", use_adapters)

        # determine all adresses from the listed adapters.
        # filter against exclusion list (to disallow docker networks, for example)
        # check if all interface names actually exist
        for a in use_adapters:
            try:
                netifaces.ifaddresses(a)
            except ValueError as error:
                raise ValueError(f'invalid adapter/interface name "{a}": {error}') from error

            self.interfaces = adapter_ips(use_adapters, EXCLUDED_NETS)
            logger.debug("publishing on interfaces IPs: %s", self.interfaces)

            self.zeroconf = zeroconf.Zeroconf(ip_version=IP_VERSION, interfaces=self.interfaces)

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

        # to make unique service instance names host1, host2, ....
        self.host_index = 0
        self.dockerclient = dockerclient
        self.interfaces = None
        self.zeroconf = None

    def __del__(self):
        pass

    def mkinfo(self,cname,port,service_type="_http._tcp.local.",props=None):
        """fill out the zeroconf ServiceInfo structure"""

        if props is None:
            props = {}

        self.host_index += 1

        return zeroconf.ServiceInfo(
            service_type,
            f"host{self.host_index}.{service_type}",
            addresses=self.interfaces,
            port=port,
            host_ttl=PUBLISH_TTL,
            server = cname,
            properties=props
        )

    def publish(self,cname,port,props):
        """ publish the given cname """
        props = props or {}

        # the FQDN needs to end with a dot. Supply one to be user friendly
        if not cname.endswith('.'):
            cname += '.'

        logger.info("publishing %s:%d",cname,port)

        info = self.mkinfo(cname,port,props=props)
        self.zeroconf.register_service(info, allow_name_change=False)
        return info

    def unpublish(self,cname,port):
        """ unpublish the given cname """

        # the FQDN needs to end with a dot. Supply one to be user friendly
        if not cname.endswith('.'):
            cname += '.'

        logger.info("unpublishing %s:%d",cname,port)
        info = self.mkinfo(cname,port)
        self.zeroconf.unregister_service(info)

    def process_event(self,event):
        """when start/stop events are received, process the container that triggered the event """
        if event['Type'] == 'container' and event['Action'] in ('start','die'):
            container_id = event['Actor']['ID']
            try:
                container = self.dockerclient.containers.get(container_id)
                self.process_container(event['Action'],container)
            except URLError as error:
                # in some cases, containers may have already gone away when we process the event.
                # consider this harmless but log an error
                logger.warning("%s",error)

    def process_container(self,action,container):
        """Run when a container triggered start/stop event.
             Checks whether the container has a label "mdns.publish" and if so, either
             registers or deregisters it"""

        hosts = container.labels.get("mdns.publish")
        txt = container.labels.get("mdns.txt")
        if txt is not None:
            txt = dict([tuple(t.split('=')) for t in txt.split(',')])

        if hosts is not None:
            for cname in hosts.split(','):
                # these may not be necessary. python-zeroconf does pretty throrough checking.
                port = 80
                if ":" in cname:
                    cname,port = cname.split(':')
                    port=int(port)
                if not self.localrule.match(cname):
                    logger.error("cannot register non-local hostname %s; rejected", cname)
                    continue
                if not self.hostnamerule.match(cname):
                    logger.error("invalid hostname %s; rejected", cname)
                    continue

                # if the cname looks valid, either register or deregister it
                if action == 'start':
                    try:
                        self.publish(cname,port,props=txt)
                    except zeroconf.BadTypeInNameException as error:
                        logger.error("zero conf: bad type in name %s: %s \
                           -- ignoring the service announcement",cname,error.args)
                    except zeroconf.NonUniqueNameException:
                        logger.error("zero conf: %s is already registered \
                                              -- ignoring the service announcement",cname)
                    except zeroconf.ServiceNameAlreadyRegistered:
                        logger.error("zero conf: service name %s is already registered \
                                              -- ignoring the service announcement",cname)
                elif action == 'die':
                    self.unpublish(cname,port)

    def run(self):
        """Initial scan of running containers and publish hostnames.
             Enumerate all running containers and register them"""

        # obtain the events stream before we iterate the containers, such that we are guaranteed
        # to get events that occur during the iteration
        events =  self.dockerclient.events(decode=True)

        logger.debug("registering running containers...")
        containers = self.dockerclient.containers.list(filters={"label":"mdns.publish"})
        for container in containers:
            self.process_container("start", container)

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
        logger.critical("%s",exception)
