# Q9828: from_chain_short_id bridge accounting divergence

## Question
Can an unprivileged attacker reach `from_chain_short_id` with crafted short_id and make locked value, released value, minted value, or fee accounting diverge across bridge state trackers, causing undercollateralized supply or funds sent to the wrong party?

## Target
- File/function: crates/sui-types/src/digests.rs::from_chain_short_id
- Entrypoint: Bridge deposit, claim, message-processing, or bridge-facing RPC flow reachable by an ordinary bridge user
- Attacker controls: short_id
- Exploit idea: Probe decimal normalization, token mapping, refund routing, rounding, and partial-failure paths for mismatched balance updates.
- Invariant to test: For every bridge action, locked, released, minted, burned, and fee-accounted amounts must remain one-to-one and fully attributable.
- Expected Immunefi impact: Critical — asset-identity or accounting divergence that breaks bridge backing guarantees or misroutes user funds.
- Fast validation: Execute boundary-value bridge transfers and partial-failure scenarios locally and compare all balance deltas across source and destination accounting.
