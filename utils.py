import ipaddress
import netifaces

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
