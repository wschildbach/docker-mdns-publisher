""" Utility functions for network adapter and address discovery """
import ipaddress
import netifaces
import zeroconf

def adapter_ips(adapters, excluded_nets):
    """return a list of all suitable ip adresses.
         Addresses are taken from non-local IPv4 addresses that do not belong to
         any of the subnets configured in excluded_nets"""

    def has_ip_v4(a):
        return netifaces.AF_INET in netifaces.ifaddresses(a)

    def non_local(ip):
        return "broadcast" in ip

    def in_excluded_networks(ip,nets):
        return any(ipaddress.ip_address(ip) in n for n in nets)

    excluded_nets = list(ipaddress.ip_network(n)
                         for n in excluded_nets.split(",") if n != "")

    return list(ip["addr"]
                for a in adapters if has_ip_v4(a)
                for ip in netifaces.ifaddresses(a)[netifaces.AF_INET]
                if non_local(ip) and not in_excluded_networks(ip["addr"],excluded_nets))

well_known_port_name = {
    80: "_http._tcp",
    443: "_http._tcp",
    515: "_printer._tcp",
    631: "_ipp._tcp",
    9100: "_pdl-datastream._tcp",
    1883: "_mqtt._tcp"
}

class IgnoredError(Exception):
    """Base class for errors that allow continued operation.
         Generally, this means that the service will not be registered
         but the daemon keeps running."""

    std = "-- ignoring the service announcement"

    def __init__(self, e, cname=None):
        if isinstance(e,zeroconf.BadTypeInNameException):
            super().__init__(f"bad type in name {cname}: {e.args} {self.std}")
        elif isinstance(e,zeroconf.NonUniqueNameException):
            super().__init__(f"server {cname} is already registered {self.std}")
        elif isinstance(e,zeroconf.ServiceNameAlreadyRegistered):
            super().__init__(f"service {cname} is already registered {self.std}")
        else:
            super().__init__(e)

class FatalError(Exception):
    """Base class for fatal errors.
         The daemon will terminate."""

    def __init__(self,e):
        super().__init__(f"{e} Terminating.")
