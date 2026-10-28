# WAN Prototype: Companion Spec

> **Status in snapl: roadmap, not started.** These are the requirements for a WAN / SD-WAN use case. Nothing here is built yet, and snapl claims none of it. Tracked in #126. A `/speckit-specify` spec comes before any implementation. Where this document and the constitution disagree, the constitution wins.
>
> **How it fits snapl.** Five points below conflict with the constitution. They're settled this way:
>
> 1. **Vendor:** SR Linux edges, not FRR. snapl stays single-vendor (Principle VI), and `ghcr.io/nokia/srlinux` pulls with no login, so the lab is still reproducible from public images. Linux hosts stay as traffic endpoints. First step of the spec: confirm which overlay tunnel SR Linux supports (GRE is unconfirmed).
> 2. **Source of Truth:** Infrahub. The YAML in section 4 shows what intent holds. It becomes an Infrahub schema and seed data, with no Git-native path.
> 3. **Deploy:** gNMI through the Executor. No Ansible.
> 4. **Rollback:** reconcile to the last known-good intent through the Orchestrator (Temporal), not "redeploy the last good tag". T-09 measures time to restore.
> 5. **CI lab:** lab tests (stages 3 to 5) run locally on macOS/OrbStack first. A self-hosted CI runner is deferred.

A personal lab that tests WAN changes before they ship: two underlays, a BGP overlay, failover tests that run in CI, and an evidence bundle for every change.

Use this file to check your Claude Code project against. Section 10 is the checklist, and section 11 is a prompt that gets Claude Code to produce a gap report.

---

## 0. Ground rules

- **Personal project.** Use your own time and hardware. Include no employer configs, names, IP plans, designs or tickets.
- **Check your employment contract's IP clause** and your employer's social media policy before the repo or a post goes public.
- **Use only public images** (FRR, Linux) in anything public, so anyone can reproduce it. Vendor images such as cEOS can't be redistributed, so keep them to a private branch if you want vendor parity.
- **Stay vendor-neutral.** This tests how WAN changes are governed. It isn't a review of a product.

## 1. What it proves

1. A WAN change can go through the same release discipline as application code: intent in Git, gates, tests, evidence, rollback.
2. Failover behaviour is measured in milliseconds on every change, not assumed.
3. Some things plain BGP can't do, such as steering away from a degraded but still-up path. The lab shows that gap and then closes it with SLA-based steering. That is the logic SD-WAN products sell.

## 2. Scope

| In | Out (say so in the README) |
| --- | --- |
| Hub and spoke with 1 hub and 2 branches | Scale: hundreds of sites, route table size, controller load |
| Two underlays: private ("MPLS-like") and internet | Real MPLS label switching (the label mechanics don't affect the testing story) |
| Tunnel overlay, eBGP per tunnel, BFD | Commercial SD-WAN controllers and orchestration |
| Hard failure, brownout, recovery, blast radius | Wireless, campus LAN, cloud on-ramps |
| Bad-change gates and automatic rollback | QoS and per-application identification (phase 3 at most) |
| Evidence bundle and delivery metrics | Encryption in the MVP (add WireGuard or IPsec in phase 2) |

## 3. Topology

Two provider "clouds". Every edge router (br1, br2, hub1) has one link into each.

```
          mpls  (private underlay, AS65100)          isp  (internet underlay, AS65200)
         /          |            \                  /          |            \
      eth1        eth1          eth1             eth2        eth2          eth2
       |            |             |                |            |             |
      br1          br2          hub1              br1          br2          hub1
   AS65011      AS65012       AS65000

   Overlay: each branch builds two tunnels to hub1, one over each underlay.
            Each tunnel has its own eBGP session with BFD. Branch-to-branch traffic goes via the hub.
   Hosts:   h-br1 on br1, h-br2 on br2, h-hub on hub1 (traffic endpoints for tests)
```

### Addressing (documentation and private ranges only)

| Item | Range |
| --- | --- |
| Site LANs | hub1 `10.1.0.0/24`, br1 `10.11.0.0/24`, br2 `10.12.0.0/24` |
| Private underlay point-to-point links | `10.255.1.0/24`, cut into /31s |
| Internet underlay point-to-point links | `198.51.100.0/24` (TEST-NET-2), cut into /31s |
| Tunnel endpoints (loopbacks) | `10.254.0.0/24` |
| Overlay tunnel interfaces | `172.16.0.0/24`, cut into /31s |
| ASNs | Private range only: 65000 to 65200 as above |

### Minimal containerlab topology

```yaml
# topology/wan.clab.yml
name: wan
topology:
  defaults:
    kind: linux
  nodes:
    hub1:  { image: quay.io/frrouting/frr:10.1.0 }   # pin the version you test with
    br1:   { image: quay.io/frrouting/frr:10.1.0 }
    br2:   { image: quay.io/frrouting/frr:10.1.0 }
    mpls:  { image: quay.io/frrouting/frr:10.1.0 }
    isp:   { image: quay.io/frrouting/frr:10.1.0 }
    h-hub: { image: ghcr.io/hellt/network-multitool }
    h-br1: { image: ghcr.io/hellt/network-multitool }
    h-br2: { image: ghcr.io/hellt/network-multitool }
  links:
    - endpoints: ["br1:eth1",  "mpls:eth1"]
    - endpoints: ["br2:eth1",  "mpls:eth2"]
    - endpoints: ["hub1:eth1", "mpls:eth3"]
    - endpoints: ["br1:eth2",  "isp:eth1"]
    - endpoints: ["br2:eth2",  "isp:eth2"]
    - endpoints: ["hub1:eth2", "isp:eth3"]
    - endpoints: ["br1:eth3",  "h-br1:eth1"]
    - endpoints: ["br2:eth3",  "h-br2:eth1"]
    - endpoints: ["hub1:eth3", "h-hub:eth1"]
```

Notes:
- FRR needs `bgpd` and `bfdd` switched on in its `daemons` file. Mount `daemons` and `frr.conf` per node with `binds`.
- Tunnels (GRE in the MVP) are Linux interfaces created at boot with the node's `exec:` commands. Generate them from intent, not by hand.
- If your CI runner blocks kernel tunnels, run the lab on a self-hosted runner, or use cEOS on your own host.

## 4. Intent: what lives in Git

Intent says what the WAN should be. The pipeline decides how to get there. Keep the two separate.

```yaml
# intent/sites.yml
sites:
  hub1: { asn: 65000, role: hub,    lan: 10.1.0.0/24 }
  br1:  { asn: 65011, role: branch, lan: 10.11.0.0/24 }
  br2:  { asn: 65012, role: branch, lan: 10.12.0.0/24 }

underlays:
  mpls: { kind: private,  preference: primary }
  inet: { kind: internet, preference: secondary }

overlay:
  topology: hub-and-spoke
  tunnel: gre                     # phase 2: wireguard or ipsec
  bfd: { interval_ms: 100, multiplier: 3 }
```

```yaml
# intent/sla.yml  (phase 2: thresholds for steering)
sla:
  default:
    max_latency_ms: 150
    max_loss_pct: 2
    max_jitter_ms: 30
    hold_down_s: 30               # stops the path flapping back and forth
```

```yaml
# intent/acceptance.yml  (tests read their pass marks from here)
acceptance:
  failover_max_loss_ms: 2000      # hard failure of the primary underlay
  brownout_steer_max_s: 10        # phase 2
  recovery_max_path_changes_60s: 1
```

If this lives inside Synapse, the mapping is: intent to an Infrahub schema, deploy to Ansible, orchestration to a Temporal workflow. Keep the Git-native YAML path too, so the public version runs without Infrahub.

> **snapl note:** in snapl, intent lives in Infrahub and deploys over gNMI through the Executor. No Ansible, and no Git-native YAML path. See the status note at the top and #126.

## 5. Pipeline and gates

```
PR opened
  └─ 1. Static checks    schema-validate intent; lint templates; check for duplicate ASNs, overlapping prefixes, missing peers
  └─ 2. Render           build configs from intent; post the config diff as a PR comment
  └─ 3. Lab deploy       containerlab up from scratch; push the rendered config
  └─ 4. Tests            section 6 catalogue; thresholds come from intent/acceptance.yml
  └─ 5. Evidence         write the bundle (section 7); upload it as a CI artifact
  └─ 6. Human approval   reviewer must not be the author; GitHub environment with a required reviewer
  └─ 7. Release          merge, then tag; the tag is the "last known good" used for rollback
```

Rules:
- `main` is protected: no direct pushes, one approving review, and status checks must pass.
- Any gate that fails stops the run. Nothing deploys past a red stage.
- **Solo repo:** let Claude Code author PRs under a bot identity (a GitHub App or machine user) and approve them yourself. That gives you real author/approver separation, and it demonstrates the AI guardrail in section 9.

## 6. Test catalogue

| ID | Test | How | Pass | Phase |
| --- | --- | --- | --- | --- |
| T-01 | Baseline | All LAN-to-LAN pairs reachable. 2 BGP sessions Established per branch. BFD up. Traceroute takes the primary tunnel | All true | 1 |
| T-02 | Intent conformance | Running config and BGP neighbours match the rendered intent | No difference | 1 |
| T-03 | Primary underlay hard fail | Take br1 eth1 down. Measure loss with a sequence-numbered UDP stream at 100 pps | Loss window ≤ `failover_max_loss_ms`; traffic on the internet tunnel | 1 |
| T-04 | Recovery | Bring br1 eth1 back up. Watch the path for 60 s | Traffic back on primary; path changes ≤ `recovery_max_path_changes_60s` | 1 |
| T-05 | Brownout | `containerlab tools netem` adds 250 ms delay and 5% loss on br1 eth1 | **MVP:** document that BGP doesn't move traffic (a known gap). **Phase 2:** traffic steers within `brownout_steer_max_s` and returns after the hold-down | 1 / 2 |
| T-06 | Hub underlay fail | Take hub1 eth1 down | Both branches converge within the T-03 threshold | 2 |
| T-07 | Blast radius | Take both br1 underlays down | br1 isolated; br2 to hub1 unaffected (zero loss) | 2 |
| T-08 | Bad change blocked before deploy | Seed PRs with: a duplicate ASN, an overlapping prefix, a schema error | Stage 1 fails; nothing deployed | 1 |
| T-09 | Bad change caught after deploy | Seed a PR that passes static checks but breaks the network (e.g. wrong peer IP) | Post-deploy tests fail; automatic rollback to the last good tag; T-01 passes again; logged as a change failure with time to restore | 1 |
| T-10 | Segmentation | VRF-lite on the edges, one tunnel pair per VRF. An IoT segment at br1 reaches only hub IoT services | IoT can't reach corp LANs; corp can't reach IoT except on allowed flows | 3 |
| T-11 | Drift | Change a device by hand with `vtysh` | The scheduled compliance run flags it within one run and opens an issue | 2 |

**Phase 2 steering:** a small probe per branch measures latency, loss and jitter on each tunnel. When `sla.yml` thresholds are breached, it lowers local preference on that path through a route-map, then waits out `hold_down_s` before moving traffic back. The point is to *test* this logic, not to build a product.

**Every failure adds a test.** If something breaks in the lab that no test caught, write the test before fixing it.

## 7. Evidence bundle

One folder per run: `evidence/<run-id>/`

```
summary.json      what changed, who, when, results (below)
change.md         purpose, scope, window, risk: copied from the PR template
diff/             rendered config diff per node
state/pre/        show outputs before the change (BGP, BFD, routes)
state/post/       show outputs after
tests.xml         JUnit results
```

```json
{
  "run_id": "2026-11-12T10-04-33Z-a1b2c3d",
  "commit": "a1b2c3d",
  "pr": 42,
  "author": "wan-bot",
  "ai_authored": true,
  "approver": "anton",
  "approved_at": "2026-11-12T10:20:11Z",
  "purpose": "Lower BFD interval on branch tunnels",
  "scope": ["br1", "br2"],
  "tests": { "passed": 9, "failed": 0, "skipped": 2 },
  "failover_loss_ms": { "T-03": 640 },
  "rollback": { "triggered": false },
  "versions": { "frr": "10.1.0", "containerlab": "x.y.z" }
}
```

Optional: sign the bundle with GitHub artifact attestations so it can be shown to be untampered.

## 8. Metrics

Computed by a script from the git log and the evidence summaries. Output to `metrics.json`, and show a small table in the README.

| Metric | Definition here |
| --- | --- |
| Deployment frequency | Release tags per week |
| Lead time for changes | First commit on the PR to a successful release tag (median) |
| Change failure rate | Releases rolled back, or fixed within 24 h, divided by all releases |
| Time to restore | Failure detected to T-01 passing again (median) |
| Defect escape rate | Defects found after deploy (T-09 style) divided by all defects found |
| Test coverage | Intent objects (sites, tunnels, policies) covered by at least one test, divided by all intent objects |
| Failover time | p50 and p95 of T-03 across runs |

## 9. AI guardrails

| AI may | AI may not |
| --- | --- |
| Draft intent changes and open PRs | Approve, merge or push to `main` |
| Write and extend tests | Deploy outside the pipeline |
| Write PR descriptions and change notes | Hold device credentials or reach lab devices directly |
| Explain a failed test or detected drift | Edit `acceptance.yml` thresholds without a human-authored PR |

Controls:
- The agent's token can push branches and open PRs only.
- Deploy requires a human approval through a GitHub environment rule.
- AI-authored PRs carry an `ai-authored` label and a link to the session.
- The evidence bundle records `ai_authored`.

## 10. Acceptance checklist

### Phase 1: needed for the first post
- [ ] Ground rules in section 0 checked: IP clause, social policy, public images only
- [ ] `containerlab deploy` builds the section 3 topology from scratch in CI
- [ ] Intent files (`sites.yml`, `acceptance.yml`) are the only place topology and pass marks are set
- [ ] Configs rendered from intent; no hand-written device configs in the repo
- [ ] Pipeline stages 1 to 7 in place, and a failed stage stops the run
- [ ] `main` protected; approver ≠ author enforced
- [ ] T-01, T-02, T-03, T-04, T-08 and T-09 automated and passing
- [ ] T-05 run and the gap documented (BGP doesn't steer on brownout)
- [ ] Evidence bundle written on every run and uploaded as a CI artifact
- [ ] Metrics script outputs at least lead time, change failure rate and time to restore
- [ ] README: diagram, scope in/out, how to run, the latest failover number
- [ ] Bot-authored PR → human approval flow demonstrated at least once

### Phase 2
- [ ] Probe-based SLA steering; T-05 passes against `sla.yml`
- [ ] T-06, T-07 and T-11 automated
- [ ] Tunnels encrypted (WireGuard or IPsec)
- [ ] All seven metrics in section 8 computed

### Phase 3
- [ ] T-10 segmentation with VRF-lite
- [ ] Evidence attestation
- [ ] Metrics trend over time in the README
- [ ] Optional: cEOS parity branch; Infrahub and Temporal path

## 11. Gap-check prompt for Claude Code

Copy this file into the repo as `docs/wan-prototype-companion.md`, then run:

```
Read docs/wan-prototype-companion.md. For every checklist item in section 10,
report done / partial / missing, naming the file, test or workflow that proves it.
Do not change any code. Output one table, then list the missing items that block
Phase 1, in the order you'd build them.
```

> **snapl note:** the paths above (`topology/wan.clab.yml`, `intent/*.yml`) don't match snapl's layout (`containerlab/<use_case>.yml`, Infrahub schemas and seed data under `packages/intent/snapl_intent/`, templates under `packages/executor/snapl_executor/templates/<use_case>/`). Until the spec adapts them, the gap check will report every item as missing.

---

## Appendix A: first post outline

- **Hook:** "We test application code before production. Most WAN changes still aren't tested."
- **What I built:** two underlays, an overlay, and a test pipeline. Personal lab, open images, link to the repo.
- **The numbers:** failover loss in ms, bad changes blocked, rollback time.
- **The gap:** plain BGP can't steer away from a brownout. What that means for how you test SD-WAN.
- **Ask:** "How do you test WAN changes today?"
- No employer names and no vendor pitch.
