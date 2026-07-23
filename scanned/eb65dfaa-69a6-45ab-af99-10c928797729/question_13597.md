# Q13597: new_consensus_commit_prologue_v2 replay or duplicate settlement acceptance

## Question
Can an unprivileged attacker reach `new_consensus_commit_prologue_v2` in `crates/sui-types/src/transaction.rs` through a normal bridge-user flow with crafted epoch, round, commit_timestamp_ms, consensus_commit_digest, cause the same bridge intent to be accepted twice across nonce, sequence, chain-domain, or finalization boundaries, and obtain double credit or unbacked bridged supply?

## Target
- File/function: crates/sui-types/src/transaction.rs::new_consensus_commit_prologue_v2
- Entrypoint: Bridge deposit, claim, message-processing, or bridge-facing RPC flow reachable by an ordinary bridge user
- Attacker controls: epoch, round, commit_timestamp_ms, consensus_commit_digest
- Exploit idea: Drive the same deposit, claim, or approval intent through an alternate ordering, encoding, or finalization state so duplicate settlement is not rejected.
- Invariant to test: A bridge intent must settle at most once globally, regardless of ordering, serialization, or cross-domain presentation.
- Expected Immunefi impact: Critical — forging or replaying native bridge settlement leading to theft or illegitimate minting of assets.
- Fast validation: On a local network, replay the same bridge intent with mutated ordering or encoding and check whether balances or minted amounts increase twice.
