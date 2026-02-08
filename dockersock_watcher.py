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

__version__ = "1.0.0"

import os
import re
import logging
from urllib.error import URLError
import ipaddress
import netifaces
import zeroconf

import docker # pylint: disable=import-error

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

    def __init__(self,dockerclient):
        """set up the mdns registry"""

        def adapter_ips(adapters):
            """return a list of all suitable ip adresses.
            Addresses are taken from non-local IPv4 addresses that do not belong to
            any of the subnets configured in EXCLUDED_NETS"""

            def has_ip_v4(a):
                return netifaces.AF_INET in netifaces.ifaddresses(a)

            def non_local(ip):
                return "broadcast" in ip

            def in_excluded_networks(ip,nets):
                return any(ipaddress.ip_address(ip) in n for n in nets)

            excluded_nets = list(ipaddress.ip_network(n)
                                 for n in EXCLUDED_NETS.split(",") if n != "")

            return list(ip["addr"]
                  for a in adapters if has_ip_v4(a)
                  for ip in netifaces.ifaddresses(a)[netifaces.AF_INET]
                  if non_local(ip) and not in_excluded_networks(ip["addr"],excluded_nets))

        logger.debug("LocalHostWatcher.__init__()")

        if IP_VERSION !=  zeroconf.IPVersion.V4Only:
            raise ValueError(f"IP_VERSION {IP_VERSION} not supported")

        # if no adapters were configured, use all interfaces
        if ADAPTERS:
            use_adapters = ADAPTERS
        else:
            use_adapters = netifaces.interfaces()
            logger.debug("publishing on all interfaces: %s", use_adapters)

        # check if all interface names actually exist
        for a in use_adapters:
            try:
                netifaces.ifaddresses(a)
            except ValueError as error:
                logger.critical('invalid adapter/interface name "%s": %s',a,error)
                raise error # and re-raise the error

        # determine all adresses from the listed adapters.
        # filter against exclusion list (to disallow docker networks, for example)
        self.interfaces = adapter_ips(use_adapters)

        logger.debug("publishing on interfaces IPs: %s", self.interfaces)

        # to make unique service instance names host1, host2, ....
        self.host_index = 0

        try:
            self.dockerclient = dockerclient
            self.zeroconf = zeroconf.Zeroconf(ip_version=IP_VERSION, interfaces=self.interfaces)

        except Exception as exception:
            # we don't really know which errors to expect here so we catch them all and re-throw
            logger.critical("%s",exception.args)
            raise exception

    def __del__(self):
        logger.info("deregistering all registered hostnames")

        if hasattr(self,"zeroconf"):
            self.zeroconf.close()
            del self.zeroconf # not strictly necessary but safe

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
        logger.info("publishing %s:%d",cname,port)
        props = props or {}

        # the FQDN needs to end with a dot. Supply one to be user friendly
        if not cname.endswith('.'):
            cname += '.'

        try:
            info = self.mkinfo(cname,port,props=props)
            self.zeroconf.register_service(info)
        except (
                zeroconf.BadTypeInNameException,
                zeroconf.NonUniqueNameException,
                zeroconf.ServiceNameAlreadyRegistered) as error:

            if isinstance(error, zeroconf.BadTypeInNameException):
                logger.error("zero conf: bad type in name %s: %s \
                                        -- ignoring the service announcement",cname,error.args)
            if isinstance(error, zeroconf.NonUniqueNameException):
                logger.error("zero conf: %s is already registered \
                                       -- ignoring the service announcement",cname)
            if isinstance(error, zeroconf.ServiceNameAlreadyRegistered):
                logger.error("zero conf: service name %s is already registered \
                                        -- ignoring the service announcement",cname)

    def unpublish(self,cname,port):
        """ unpublish the given cname """
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
                    except KeyError:
                        logger.warning("registering previously registered %s",cname)
                elif action == 'die':
                    try:
                        self.unpublish(cname,port)
                    except KeyError:
                        logger.warning("unregistering previously unregistered %s",cname)

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

if __name__ == '__main__':
    logger.info("docker-mdns-publisher daemon v%s starting.", __version__)

    LOCAL_WATCHER = LocalHostWatcher(docker.from_env())
    LOCAL_WATCHER.run() # this will never return

    # we should never get here because run() loops indefinitely
    assert False, "executing unreachable code"
