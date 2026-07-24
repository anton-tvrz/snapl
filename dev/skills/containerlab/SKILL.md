# Containerlab — Virtual Network Lab Skill

## Metadata
- name: containerlab
- triggers: containerlab, clab, lab, topology, SR Linux image, OrbStack
- project: snapl

## What is Containerlab?

Containerlab deploys container-based network topologies. snapl uses it to run Nokia SR Linux switches locally on macOS via OrbStack (Docker).

## Topology YAML Structure

Topologies are defined in `containerlab/`:

```yaml
name: dc-fabric
topology:
  kinds:
    nokia_srlinux:
      image: ghcr.io/nokia/srlinux:latest
  nodes:
    spine01:
      kind: nokia_srlinux
      type: ixr-d3
    leaf01:
      kind: nokia_srlinux
      type: ixr-d2
    leaf02:
      kind: nokia_srlinux
      type: ixr-d2
  links:
    - endpoints: ["spine01:e1-1", "leaf01:e1-49"]
    - endpoints: ["spine01:e1-2", "leaf02:e1-49"]
    - endpoints: ["spine01:e1-3", "leaf01:e1-50"]
    - endpoints: ["spine01:e1-4", "leaf02:e1-50"]
```

## Lab Topologies by Use Case

| File | Use Case | Nodes |
|------|----------|-------|
| `dc-fabric.clab.yml` | Datacenter Fabric | Spine-leaf eBGP |
| `client-edge.clab.yml` | Client Edge | Edge routers |
| `sd-wan.clab.yml` | SD-WAN | Hub/spoke |
| `wan.clab.yml` | WAN | Backbone routers |

## Lab Lifecycle Commands

```bash
# Deploy lab
sudo containerlab deploy -t containerlab/dc-fabric.clab.yml

# Destroy lab
sudo containerlab destroy -t containerlab/dc-fabric.clab.yml
```

## Node Access

```bash
# Nokia SR Linux CLI
docker exec -it clab-dc-fabric-spine01 sr_cli

# DNS names (from macOS)
clab-dc-fabric-spine01
clab-dc-fabric-leaf01
clab-dc-fabric-leaf02
```

## Management Network

- Network: `clab-snapl` / `172.20.21.0/24` — dedicated bridge, static per-node
  pins .11–.16 (Issue #90; isolated from other labs on this host)
- Access from macOS host via OrbStack network bridge
- gNMI: plaintext on port 57400 on every node, reached by container DNS name
  (`clab-dcfabric-spine-01` … `clab-dcfabric-leaf-04`)

## OrbStack-Specific Notes (macOS)

- OrbStack provides Docker runtime on Apple Silicon
- Container DNS resolution works from macOS host
- No need for port forwarding — direct container network access
- Memory: allocate at least 10GB for full stack (Infrahub + Temporal + lab)

## Common Mistakes

- Hardcoding management IPs (use DNS names)
- Not waiting for BGP convergence after lab deploy
- Missing OrbStack routing setup
- Trying to use `localhost` instead of container DNS names
