# Spec 013: Live Captures C1 to C5

**Run**: 2026-11-24, 14:05 to 14:08 UTC, on the dcfabric lab's leaf-04 (SR Linux v26.3.1-410), main at `cb80989`.
**Answers**: the five questions for planning in `spec.md`. The plan's `research.md` cites this file.

## How

Config that intent does not hold was added to leaf-04 by hand, as an operator would, with `sr_cli -ec`:

- `ethernet-1/7`: description `c-probe`, admin-state enable, `192.0.2.1/31` on subinterface 0 (the documentation range), and `ethernet-1/7.0` in network instance `default`;
- BGP group `c-probe-group`, neighbour `192.0.2.0` (peer-as 65099) in it, and neighbour `192.0.2.3` (peer-as 65098) in the fabric's group `underlay-ipv4@leaf-04` with the fabric's import and export policy `underlay-loopbacks`.

`snapl scan` then reported leaf-04 drifted with nine undesired items: the port's description, address, prefix length and `enabled`; each neighbour's `peer_asn` and `peer_group`; and `/network-instance[name=default]/interface[name=ethernet-1/7.0]` (a `routed_interface`, #315).

Each probe was a gNMI Set with only `delete` entries, sent with pygnmi's `gNMIclient.set(delete=[…])` as the executor dials (`insecure`, port 57400), and read back with a `json_ietf` GET. Everything added was removed; afterwards leaf-04 had no `ethernet-1/7`, no probe group or neighbour, its two fabric sessions were still established (up 2 h 51 m), and the scan read 6 of 6 clean.

## C1: is a multi-delete Set atomic?

**Yes.** A Set is validated and committed as one transaction.

- A Set deleting `/interface[name=ethernet-1/7]/description` and `/interface[name=ethernet-1/7]`, while `default` still bound `ethernet-1/7.0`, was rejected as a whole. The description was still there afterwards. The error is a gRPC `FailedPrecondition` that names the path:

  ```
  Error in path: .network-instance{.name=="default"}.interface{.name=="ethernet-1/7.0"}
      [FailedPrecondition] subinterface ethernet-1/7.0 not found
  ```

- A Set deleting `/interface[name=ethernet-1/7]`, then `/network-instance[name=default]/interface[name=ethernet-1/7.0]`, then the group, was accepted: the order inside a Set does not matter, only the state at commit.
- A delete of a path that does not exist (`/interface[name=ethernet-1/8]`) is accepted and changes nothing, so a retried removal is safe.

**For the plan**: one Set per device. A rejection leaves the device as it was, so a failed removal has nothing half-applied to report. The interface and its `routed_interface` binding must be deleted in the same Set; deleting the interface alone is rejected (see "Spec questions" below).

## C2: what does a deleted interface look like?

**Absent from config, and the scan reads it as clean.** After its entry was deleted, a config GET of `/interface[name=ethernet-1/7]` returned nothing. A state GET of its `admin-state`, `mtu` and `description` returned only `admin-state: disable`, which the diff's `device_defaults` already ignore (`enabled: False`, #175). The scan after the deletes read leaf-04 clean.

**For the plan**: deleting the interface entry is the right target for an undesired port, including one that only carries `enabled: true` (spec edge case, Q2). No defaults are left behind for the diff to report.

## C3: does an address delete need its prefix escaped?

**No.** `/interface[name=ethernet-1/7]/subinterface[index=0]/ipv4/address[ip-prefix=192.0.2.1/31]` was accepted as written, with the `/` inside the key, and the address was gone afterwards.

It left `ipv4 { admin-state enable }` on subinterface 0. The diff has no field for it (`ENTITY_FIELD_MAP`), so it should not read as undesired. This was not scanned on its own, since the interface was deleted next.

## C4: does deleting a neighbour need anything else removed?

**No.** A neighbour's delete is accepted on its own, whether it is a group's only member or names import and export policies. What it referenced stays:

- the group, now empty, stays configured (`admin-state enable`);
- the policy stays, unchanged.

The reverse is refused: deleting a group that a neighbour still names is rejected, with the referrer in the error:

```
Error in path: .network-instance{.name=="default"}.protocols.bgp.group{.group-name=="c-probe-group"}
    [FailedPrecondition] Deleting object which is referenced by .network-instance{.name=="default"}.protocols.bgp.neighbor{.peer-address=="192.0.2.0"}.peer-group
```

**For the plan**: removing an undesired neighbour is one delete. An empty group it leaves behind is not reported (the diff has no group entity) and is not removed. A policy is reported as undesired when intent does not hold it (`routing_policy`, #317); deleting one a remaining neighbour still names would, by the same rule as the group, be refused (not tried).

## C5: how soon does a GET reflect a delete?

**At once.** Ten rounds of: create `ethernet-1/7` with a description, delete its entry, then GET its description until it is gone. In all ten the first GET after the Set returned found it gone. A Set took 207 to 277 ms and a GET about 210 ms, each including the connection.

**For the plan**: the verifying scan can run as soon as the Set returns. No wait or retry is needed for the device to catch up.

## Spec questions this raises

- **The `routed_interface` binding.** Spec 013 lists the kinds it removes as interfaces, their IPv4 addresses and BGP neighbours (Out of Scope). Since it was written, the scan also reports `routed_interface` (#315) and `routing_policy` (#317) as undesired. C1 shows a port bound to a network instance cannot be deleted without its binding, so removing an undesired routed port needs the binding in the same Set. The plan has to say whether `routed_interface` joins the removable kinds, or the port's removal carries its binding.
- **Leftovers that are not reported.** An empty peer group (C4) and a subinterface's `ipv4 admin-state` (C3) stay on the device after a removal, and the diff does not see them. The spec's success criteria only ask for no undesired items in the next scan, which holds. Whether they matter is the maintainer's call.
