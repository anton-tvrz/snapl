# Quickstart: Site Edge — A Segmentation Rule as Intent

**Feature**: 008-site-edge
**Date**: 2026-10-28

> **This describes the feature once it is built.** Nothing here runs today. There is no site_edge seed, template or lab yet, and implementation waits on #103. The `sr_cli` commands that make the hand changes are **provisional**: their exact syntax is confirmed while taking captures C3 to C6 (research R16), and this file is updated then.

## Prerequisites

- `uv sync --all-groups`
- The dev stack and a worker, as for dcfabric (see `docs/demo-scenarios.md`, "One-time setup")
- Lab credentials: `admin` / `NokiaSrl1!` <!-- pragma: allowlist secret -->

## Unit tests (no infrastructure)

```bash
uv run invoke test-unit
uv run pytest tests/unit/test_observability/test_diff.py tests/unit/test_orchestrator/test_render_diff_contract.py -m unit -v
```

## Bring up the site_edge lab and Source of Truth

```bash
uv run invoke demo.up --use-case site_edge   # deps -> site_edge lab -> provision + seed -> preflight
uv run invoke orchestrator.start             # own terminal
```

The preflight must list `site-edge-01` (`172.20.22.11`) and `carrier-a` (`172.20.22.21`) as reachable on gNMI. The site_edge lab runs on its own management network (`172.20.22.0/24`), so it can run alongside dcfabric.

## Demo arc: a deny rule removed by hand (User Story 2)

```bash
# 1. Converge first — never induce drift on an unconverged lab
snapl deploy site-edge-01
snapl deploy carrier-a
snapl scan --use-case site_edge                 # expect: clean, exit 0

# 2. Someone removes the deny by hand (provisional syntax)
docker exec clab-site-edge-site-edge-01 sr_cli -ec \
  'delete / acl acl-filter ot-ingress type ipv4 entry 20'

# 3. snapl sees it
snapl scan --use-case site_edge                 # expect: site-edge-01 drifted, exit 2
#    .../acl-filter[name=ot-ingress][type=ipv4]/entry[sequence-id=20]/action  desired=drop  actual=None

# 4. Heal and confirm
snapl reconcile site-edge-01 --yes
snapl scan --use-case site_edge                 # expect: clean, exit 0
```

## Other hand changes (User Stories 3 and 4)

Run each from a converged lab.

| Change (provisional `sr_cli -ec '...'`) | Expected scan | After `snapl reconcile site-edge-01` |
| --- | --- | --- |
| `set / acl acl-filter ot-ingress type ipv4 entry 20 action accept` | `entry 20 / action` desired `drop`, actual `accept` | clean |
| `set / acl acl-filter ot-ingress type ipv4 entry 10 match ipv4 destination-ip prefix 10.50.0.0/16` | `entry 10 / destination_prefix` both values | clean |
| `delete / acl interfaces interface ethernet-1/3.0 input acl-filter ot-ingress type ipv4` | binding on `ethernet-1/3.0` missing | clean |
| `set / acl acl-filter ot-ingress type ipv4 entry 5 action accept` | `entry 5` reported **undesired** | **still reported** under the default answer to Q3. Reconcile does not remove it (#65). |

## Regression check on dcfabric (SC-005)

```bash
uv run invoke demo.up                           # dcfabric (default)
snapl scan --use-case dcfabric                  # expect: 6 clean, 0 items — ACL collection adds nothing
```

## Capturing fixtures (implementers)

```bash
export SRLINUX_PASSWORD='NokiaSrl1!'  # pragma: allowlist secret
uv run python dev/scripts/capture_srlinux_acl_state.py --node 172.20.22.11 --case applied
# cases: baseline | applied | deny-deleted | permit-inserted | action-flipped | binding-moved
```

Each run writes `tests/unit/test_orchestrator/fixtures/srlinux_v26_3_1_acl_<case>.json`. Record its provenance in that directory's README. The PR body states which captures were run and what was observed (Principle III).
