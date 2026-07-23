# Q9385: encode bridge accounting divergence

## Question
Can an unprivileged attacker reach `encode` with crafted version, chain_identifier and make locked value, released value, minted value, or fee accounting diverge across bridge state trackers, causing undercollateralized supply or funds sent to the wrong party?

## Target
- File/function: crates/sui-types/src/coin_reservation.rs::encode
- Entrypoint: Bridge deposit, claim, message-processing, or bridge-facing RPC flow reachable by an ordinary bridge user
- Attacker controls: version, chain_identifier
- Exploit idea: Probe decimal normalization, token mapping, refund routing, rounding, and partial-failure paths for mismatched balance updates.
- Invariant to test: For every bridge action, locked, released, minted, burned, and fee-accounted amounts must remain one-to-one and fully attributable.
- Expected Immunefi impact: Critical — asset-identity or accounting divergence that breaks bridge backing guarantees or misroutes user funds.
- Fast validation: Execute boundary-value bridge transfers and partial-failure scenarios locally and compare all balance deltas across source and destination accounting.
