# A docker mDNS publisher

docker-mdns-publisher is a daemon designed to work with docker, best used with `docker compose`.

It sits in the background, waiting for containers that are started. If the containers expose
a specific label, then the daemon interprets the label as a local hostname and registers it
with a local mdns server (python-zeroconf).

This makes it very convenient to run docker containers that expose services to the local
network, using .local domain labels to access them.

## Deploying

Create an empty directory, and create a compose.yml file:

```
services:
  docker-mdns-publisher:
    image: ghcr.io/wschildbach/docker-mdns-publisher:1
    read_only: true
    restart: on-failure:10
    network_mode: host # unless port 5353 is free, we need host networking
    environment:
      - LOG_LEVEL=INFO # INFO is the default
      - PYTHONUNBUFFERED=1 # for prompter logging
      - "EXCLUDED_NETS=172.16.0.0/16" # exclude docker networks (adapt to your machine)
    volumes:
      # we need read access to the docker socket to read container labels
      - /var/run/docker.sock:/var/run/docker.sock:ro
```

Then issue `docker compose up -d`, and/or make sure that whenever your system starts up, this service gets started too.
Details depend on your distribution.

When the daemon starts up, expect to see something like
```
docker-mdns-publisher-1  | INFO:docker-mdns-publisher:docker-mdns-publisher daemon v**** starting.
```
in the log. Depending on whether any services are running which are configured to be registered, you will also see lines like
```
docker-mdns-publisher-1  | INFO:docker-mdns-publisher:publishing test1.local
```

### Configuration

**TTL**
 > This sets the TTL for the mDNS publication, in seconds. The default is an hour.

**LOG_LEVEL**
> This sets the verbosity of logging. Use the [log levels of the python logging module](https://docs.python.org/3/library/logging.html#logging-levels)
(CRITICAL, ERROR, WARNING, INFO, DEBUG,TRACE). The default is INFO.

**ADAPTERS**
> A list of adapters on which the mdns server listens and publishes. If empty, uses all non-local IPv4 adapters.

**EXCLUDED_NETS**
> A comma-separated list of networks to exclude. This can be used to exclude docker-internal networks.

## Using with your services

In your service compose file definition, add a label `mdns.publish=<myhost>.local` and restart your
service/container (replace `<myhost>` with whatever name you want to give your service).

The daemon then publishes an mdns service record, with `<myhost>._http._tcp.local`,
using the interfaces configured above. The server field is set to `<myhost>.local`.

When the container is stopped, the host is unpublished. Depending on the TTL, it may take some
time until the change becomes effective.

### Further configuration

* change the default port by using `mdns.publish=host:port` notation. The default port is 80.
* the service type is set according to the port, but can be overridden using a `mdns.servicetype=<_myservicetype._tcp>` label.
* txt records can be added by using `mdns.txt=key1=value1,key2=value2` notation.

Obviously, you could also supply labels in the Dockerfile of your service, or on the command line, if that is more convenient.

### Example

```
services:
  test:
    image: alpine
    command: "sleep 15"
    labels:
      - mdns.publish=test2.local:80 # just for demo purposes, port 80 will be assumed by default
      - mdns.servicetype=_http._tcp # just for demo purposes, this is the default with port 80
      - mdns.txt=version=1,path=/home/bin/test.exe
```

## Development and debugging

To enable debugging on the daemon, set the `LOG_LEVEL` environment variable.
`LOG_LEVEL` must be set to one of the [standard python log levels](https://docs.python.org/3/library/logging.html#logging-levels).

* DEBUG outputs lots of debugging in the daemon itself. In this mode, the daemon also adds txt records to all mdns registrations that give the time and container id when and where the publication was triggered.
* TRACING enables debug output for the zeroconf library in addition.

You can set this in the compose.yml file:

```
    environment:
      - LOG_LEVEL=DEBUG
```

The compose.yml file provides a few test services which register themselves. Start the daemon using
`docker compose --profile debug up` and the test services will start up together with the daemon.
The test services simply wait for a predetermined time, then terminate.

## Credits
The project took inspiration from [github/hardillb/traefik-avahi-helper](https://github.com/hardillb/traefik-avahi-helper)
which in turn borrows from [github/alticelabs/mdns-publisher](https://github.com/alticelabs/mdns-publisher).
It now relies on the [python-zeroconf](https://github.com/python-zeroconf/python-zeroconf) library.

Many thanks to [Andreas Schildbach](https://github.com/schildbach) for feedback and suggestions to this project.
