# Quickstart: Remove Undesired Config

This is how to use `snapl remove`, and the live run the implementation PR records as evidence (constitution III, research R12).

## Use

```bash
uv run snapl scan                       # leaf-04 drifted, with undesired items
uv run snapl remove leaf-04             # preview: the deletes, the refusals; writes nothing
uv run snapl remove leaf-04 --apply     # preview, confirm, remove, verify
uv run snapl audit --device leaf-04     # the removal, its actor, deletes and verification
```

Scheduled runs never remove, in any mode. Undesired-only devices stay excluded from repair with `undesired_only`.

## Live evidence (SC-001 to SC-005)

These steps run on the dcfabric lab's leaf-04, with the worker running from the branch, the drift schedule paused for the run (`snapl schedule pause --reason "spec 013 evidence"`), and the fabric clean to start with. Set this up first:

```bash
L() { docker exec clab-dcfabric-leaf-04 sr_cli -ec "$1"; }
CFG() { docker exec clab-dcfabric-leaf-04 sr_cli "info flat" ; }
```

### 1. Hand-add config outside intent (SC-001's three kinds)

```bash
L "set / interface ethernet-1/7 description spec013-probe"
L "set / interface ethernet-1/7 admin-state enable"
L "set / network-instance default protocols bgp neighbor 192.0.2.0 peer-as 65099 peer-group underlay-ipv4@leaf-04"
L "set / interface ethernet-1/49 subinterface 0 ipv4 address 192.0.2.1/31"
uv run snapl scan        # leaf-04 drifted; undesired items for each of the three
```

Before the neighbour is added, check that the group name matches the lab with `L "info flat network-instance default protocols bgp group *"`.

### 2. Preview twice; config unchanged (SC-002)

```bash
CFG > before.txt
uv run snapl remove leaf-04; echo "exit $?"     # three deletes; exit 2
uv run snapl remove leaf-04 --json > plan.json
CFG > after.txt
diff before.txt after.txt && echo "SC-002: identical"
```

### 3. The device changes at the prompt (SC-004)

```bash
uv run snapl remove leaf-04 --apply
# At the prompt, in another terminal:
L "set / interface ethernet-1/8 description spec013-late"
# Answer y. Expect "leaf-04 changed since the preview; nothing was removed", the
# new plan with four deletes, and exit 1.
CFG | grep -c spec013     # both descriptions still there: nothing was written
```

### 4. Remove (SC-001)

```bash
uv run snapl remove leaf-04 --apply --yes; echo "exit $?"   # verified; exit 0
uv run snapl scan                                            # leaf-04 clean
L "info flat interface ethernet-1/7"                         # nothing
L "info flat network-instance default protocols bgp neighbor 192.0.2.0"   # nothing
L "info flat interface ethernet-1/49 subinterface 0"         # the intended address only, plus ipv4 admin-state (R4)
docker exec clab-dcfabric-leaf-04 sr_cli "show network-instance default protocols bgp neighbor"   # fabric sessions established
```

### 5. The record (SC-005)

```bash
uv run snapl audit --device leaf-04      # PlanRemoval runs and the RemoveConfig run: actor, deletes, verified
```

On the dashboard (`snapl dashboard`), the run list shows `RemoveConfig` with its trigger `operator`. After the next scheduled scan, leaf-04's undesired episode is closed by a removal. Resume the schedule first: `snapl schedule resume`.

### 6. Guards on the real device

```bash
L "set / interface ethernet-1/7 description p"
L "set / interface ethernet-1/7 subinterface 0 ipv4 address 192.0.2.5/31"
L "set / network-instance default interface ethernet-1/7.0"
uv run snapl remove leaf-04 --apply --yes   # the port and its binding go in one Set (R3); verified
```

### Afterwards

- leaf-04 has no `spec013` config.
- `snapl scan` reads 6 of 6 clean.
- The schedule is resumed.
- `evidence.md` records each step's output.
- The captured `remove` responses and a `RemoveConfig` audit trail become fixtures (constitution III).
