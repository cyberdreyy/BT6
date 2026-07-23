# Q602: get_committee_voting_power_by_name replay or duplicate settlement acceptance

## Question
Can an unprivileged attacker reach `get_committee_voting_power_by_name` in `crates/sui-bridge/src/utils.rs` through a normal bridge-user flow with crafted bridge_committee, system_state, cause the same bridge intent to be accepted twice across nonce, sequence, chain-domain, or finalization boundaries, and obtain double credit or unbacked bridged supply?

## Target
- File/function: crates/sui-bridge/src/utils.rs::get_committee_voting_power_by_name
- Entrypoint: Bridge deposit, claim, message-processing, or bridge-facing RPC flow reachable by an ordinary bridge user
- Attacker controls: bridge_committee, system_state
- Exploit idea: Drive the same deposit, claim, or approval intent through an alternate ordering, encoding, or finalization state so duplicate settlement is not rejected.
- Invariant to test: A bridge intent must settle at most once globally, regardless of ordering, serialization, or cross-domain presentation.
- Expected Immunefi impact: Critical — forging or replaying native bridge settlement leading to theft or illegitimate minting of assets.
- Fast validation: On a local network, replay the same bridge intent with mutated ordering or encoding and check whether balances or minted amounts increase twice.
