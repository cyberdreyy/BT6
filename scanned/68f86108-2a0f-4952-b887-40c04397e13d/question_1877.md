# Q1877: record_end_of_message_quorum_time_metric replay or duplicate settlement acceptance

## Question
Can an unprivileged attacker reach `record_end_of_message_quorum_time_metric` in `crates/sui-core/src/authority/authority_per_epoch_store.rs` through a normal bridge-user flow with crafted transaction contents, object references, gas settings, request parameters, and sequencing, cause the same bridge intent to be accepted twice across nonce, sequence, chain-domain, or finalization boundaries, and obtain double credit or unbacked bridged supply?

## Target
- File/function: crates/sui-core/src/authority/authority_per_epoch_store.rs::record_end_of_message_quorum_time_metric
- Entrypoint: Bridge deposit, claim, message-processing, or bridge-facing RPC flow reachable by an ordinary bridge user
- Attacker controls: transaction contents, object references, gas settings, request parameters, and sequencing
- Exploit idea: Drive the same deposit, claim, or approval intent through an alternate ordering, encoding, or finalization state so duplicate settlement is not rejected.
- Invariant to test: A bridge intent must settle at most once globally, regardless of ordering, serialization, or cross-domain presentation.
- Expected Immunefi impact: Critical — forging or replaying native bridge settlement leading to theft or illegitimate minting of assets.
- Fast validation: On a local network, replay the same bridge intent with mutated ordering or encoding and check whether balances or minted amounts increase twice.
