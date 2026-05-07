# SR Linux gNMI — Nokia Device Configuration Skill

## Metadata
- name: srlinux-gnmi
- triggers: SR Linux, Nokia, gNMI, pygnmi, device config, deploy, interface, BGP
- project: snapl

## Critical Rule

Nokia SR Linux uses **structured YANG-modelled JSON**, NOT CLI commands. Never generate CLI-style config. Always produce gNMI-ready JSON paths.

## gNMI SET Pattern (pygnmi)

Used by `snapl_executor` (Executor NAF block):

```python
from pygnmi.client import gNMIclient

def deploy_config(host: str, port: int, config: dict) -> None:
    with gNMIclient(
        target=(host, port),
        username="admin",
        password="<SRLINUX_PASSWORD>",  # pragma: allowlist secret
        insecure=True,
    ) as gc:
        result = gc.set(update=[
            (
                "/",  # root path
                config,  # YANG-modelled JSON
            )
        ])
```

## gNMI GET Pattern (State Validation)

Used by `snapl_collector` (Collector NAF block):

```python
def get_state(host: str, port: int, path: str) -> dict:
    with gNMIclient(
        target=(host, port),
        username="admin",
        password="<SRLINUX_PASSWORD>",  # pragma: allowlist secret
        insecure=True,
    ) as gc:
        return gc.get(path=[path], encoding="json_ietf")
```

## SR Linux JSON Config Structure

Interface configuration example (NOT CLI — this is the correct format):

```json
{
    "interface": [
        {
            "name": "ethernet-1/1",
            "admin-state": "enable",
            "subinterface": [
                {
                    "index": 0,
                    "ipv4": {
                        "admin-state": "enable",
                        "address": [
                            {
                                "ip-prefix": "10.0.0.0/31"
                            }
                        ]
                    }
                }
            ]
        }
    ]
}
```

## Interface Naming Convention

- Physical: `ethernet-1/1`, `ethernet-1/2`, etc.
- Loopback: `lo0`
- Subinterfaces: indexed from 0 (e.g., `ethernet-1/1.0`)
- System interface: `system0`

## BGP Configuration Path

```
/network-instance[name=default]/protocols/bgp
```

## Common Mistakes

- Generating CLI commands (`set interface ethernet-1/1 ...`) instead of JSON
- Using Cisco-style naming (`GigabitEthernet0/0` instead of `ethernet-1/1`)
- Hardcoding management IPs (use Containerlab DNS names)
- Missing `insecure=True` for self-signed certs in lab
- Wrong gNMI port (each node gets a unique port)
