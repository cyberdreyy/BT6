# Q9643: new_from_auth_sign_infos replay or duplicate settlement acceptance

## Question
Can an unprivileged attacker reach `new_from_auth_sign_infos` in `crates/sui-types/src/crypto.rs` through a normal bridge-user flow with crafted auth_sign_infos, committee, cause the same bridge intent to be accepted twice across nonce, sequence, chain-domain, or finalization boundaries, and obtain double credit or unbacked bridged supply?

## Target
- File/function: crates/sui-types/src/crypto.rs::new_from_auth_sign_infos
- Entrypoint: Bridge deposit, claim, message-processing, or bridge-facing RPC flow reachable by an ordinary bridge user
- Attacker controls: auth_sign_infos, committee
- Exploit idea: Drive the same deposit, claim, or approval intent through an alternate ordering, encoding, or finalization state so duplicate settlement is not rejected.
- Invariant to test: A bridge intent must settle at most once globally, regardless of ordering, serialization, or cross-domain presentation.
- Expected Immunefi impact: Critical — forging or replaying native bridge settlement leading to theft or illegitimate minting of assets.
- Fast validation: On a local network, replay the same bridge intent with mutated ordering or encoding and check whether balances or minted amounts increase twice.
