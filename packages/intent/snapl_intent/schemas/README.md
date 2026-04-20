# Intent schemas

Infrahub schema YAML files organized for the 3-batch load strategy used by
``InfrahubIntentStore.provision_schema``. Files are discovered from disk —
there is no in-code registry.

## Batch layout

- ``base/`` — Batch 1: foundation schemas from the Infrahub
  [schema-library](https://github.com/opsmill/schema-library) (Apache-2.0).
  Files: ``dcim.yml``, ``ipam.yml``, ``location.yml``, ``organization.yml``.
- ``extensions/`` — Batch 2: schema-library extensions for routing + VRFs.
  Files: ``routing.yml``, ``routing_bgp.yml``, ``vrf.yml``.
- ``*.yml`` in this directory — Batch 3: project-specific extensions.
  Files: ``network_device.yml``, ``network_interface.yml``,
  ``business_intent.yml``.

Dependencies flow downward: Batch 2 references Batch 1 types, Batch 3 extends
Batch 1 and Batch 2 types. Loading out of order causes Infrahub to reject the
schema with "unknown kind" errors.

## Attribution

The files in ``base/`` and ``extensions/`` are vendored from the Infrahub
schema-library at the ``stable`` tag and are © OpsMill under Apache-2.0. Keep
them unmodified — project overrides live in the Batch 3 files at this
directory's root.
