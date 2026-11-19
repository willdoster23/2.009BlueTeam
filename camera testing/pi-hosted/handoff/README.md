# Lab access material

The full local handoff bundle contains Pi access details and a historical network-key copy under `private/`. Automatic approval review rejected publishing plaintext passwords/keys, so they are not included in this public repository.

See the [main handoff](../HANDOFF.md) for SSH users/addresses and shared-key setup. The actual deployed key belongs at `/opt/blueteam-network/network.key` on both Pis. Preserve it. A locally recovered historical key could not be verified against the current Pi pair because fresh SSH audits timed out.

A fresh pair can use a newly generated shared key installed identically on both Pis. Do not rotate only one Pi’s key.
