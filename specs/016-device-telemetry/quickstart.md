# Quickstart: Device Telemetry on the Lab

These are the checks only the lab can make. Each is a **Needs the lab** task in tasks.md. Run them in order, with the stack up (`uv run invoke dev.deps`), the lab deployed, and the fabric converged (`snapl reconcile --drifted --yes`).

Lab addresses: spine-01 172.20.21.11, spine-02 .12, leaf-01 .13 to leaf-04 .16. Write evidence to `.snapl/lab/<date>/` and the summary to `specs/016-device-telemetry/evidence.md`.

1. **Fault captures (fixtures).** With `dev/scripts/capture_srlinux_telemetry.py`, capture leaf-01's `ethernet-1/49` while it is disabled on the device and re-enabled (`sr_cli` in `clab-dcfabric-leaf-01`: `enter candidate`, `set interface ethernet-1/49 admin-state disable`, `commit now`; then `enable`). Capture spine-01 while `docker pause clab-dcfabric-leaf-01` holds for 60 s, then `docker unpause`. Keep both as fixtures with a README line each, and add a test that reads each.

2. **The worker streams (C2).** Restart the worker on main. Within 30 s, its log names six streams `streaming`. `curl -s localhost:18091/metrics | grep -c '^snapl_\(interface\|bgp\|device\)_'` gives the series count. Expect 146 while #311 is open (16 links × 7 + 6 loopbacks + 8 session ends × 2 + 6 × 2), 162 after it. That is SC-004.

3. **Today's fault is flagged (SC-003, before #311; US2 scenario 3).** The fabric panel shows both spines in sync and unhealthy. `snapl status` shows `4 healthy, 2 unhealthy (spine-01, spine-02)`. `snapl audit --device spine-01` shows one `health_changed` with four reasons `BGP 10.10.x.y active (expected established)`. No leaf is flagged. CPU and memory flag nothing.

4. **An interface fault (SC-001).** Disable leaf-01's `ethernet-1/49` on the device, as in item 1, and note the time. The drawer shows it down within 30 s, and leaf-01 is flagged within 60 s with `ethernet-1/49 down (expected up)`. spine-01's `ethernet-1/1` goes down with it, and spine-01's reasons grow by one. Disabling it on the device is also drift, so the next scan shows leaf-01 drifted as well as unhealthy: two separate marks. (Item 3's spines are the other case, in sync and unhealthy.) Reconcile leaf-01: the scan returns to in sync, the interface comes up, and the flag clears within 60 s.

5. **A paused device (SC-006).** `docker pause clab-dcfabric-leaf-03` for 60 s. leaf-03 reads `unknown`, "no telemetry since …", never `unhealthy`. `docker unpause`: the stream is `streaming` again within 60 s, with no worker restart.

6. **Scan time (SC-005).** As spec 015 T044: the median of at least 10 scheduled scans with `SNAPL_TELEMETRY=off`, then 10 with it on. The difference is at most 10%.

7. **After #311 (SC-002, SC-003).** Once #311 is merged and the fabric reconciled: all six devices healthy, and every session end `established` with prefixes received. Disable one session's neighbour on spine-01 (`admin-state disable`): both ends show not established within 30 s and both devices are flagged.

8. **An archive across a fault (SC-008).** Run item 4 again, then `uv run invoke history.archive`. Stop the stack and open the archive with `snapl dashboard --archive`: the health history shows leaf-01 flagged and cleared, and the network views show the interface down over that time. Make a snapshot too and note its size against spec 015's 3.2 MB.
