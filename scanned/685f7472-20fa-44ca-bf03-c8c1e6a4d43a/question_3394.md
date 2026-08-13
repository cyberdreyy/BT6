# Q3394: enable_staked_oracle_onramp: staked-oracle transition can be used to brick live collateral paths [an-attacker-signer-targeting-a] [identity-vs-shape]

## Question
Can an unprivileged attacker invoke `enable_staked_oracle_onramp` with an attacker signer targeting a group with live staked banks so `enable_staked_oracle_onramp` performs a staked-oracle transition that bricks or misprices live collateral paths, violating `staked onramp mode transitions must be strictly authorized and leave no mixed pricing state for live collateral` and causing `High: live collateral mispricing or durable user freeze`? Focus specifically on exact oracle identity and lineage, not merely owner/type compatibility.

## Target
- File/function: `programs/marginfi/src/instructions/marginfi_group/on_ramp_transition.rs` / `enable_staked_oracle_onramp`
- Entrypoint: `enable_staked_oracle_onramp`
- Attacker controls: an attacker signer targeting a group with live staked banks
- Exploit idea: This is in scope when caused by a public bypass, not by an admin making a policy choice. Focus specifically on exact oracle identity and lineage, not merely owner/type compatibility.
- Invariant to test: staked onramp mode transitions must be strictly authorized and leave no mixed pricing state for live collateral
- Expected Immunefi impact: High: live collateral mispricing or durable user freeze
- Fast validation: Exercise the transition under attacker-controlled auth/binding attempts and assert it cannot affect live banks without the intended role and full validation. Substitute same-type oracle-related accounts and assert every non-canonical identity is rejected even if shape-compatible.
