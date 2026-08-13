# Q3386: enable_staked_oracle_onramp: oracle-config validation checks shape but not exact key lineage [replay-of-a-previously-valid] [identity-vs-shape]

## Question
Can an unprivileged attacker use `enable_staked_oracle_onramp` with replay of a previously valid mode-switch layout so `enable_staked_oracle_onramp` accepts an oracle-related account with the right shape but wrong lineage, violating `staked onramp mode transitions must be strictly authorized and leave no mixed pricing state for live collateral` and causing `High: live collateral mispricing or durable user freeze`? Focus specifically on exact oracle identity and lineage, not merely owner/type compatibility.

## Target
- File/function: `programs/marginfi/src/instructions/marginfi_group/on_ramp_transition.rs` / `enable_staked_oracle_onramp`
- Entrypoint: `enable_staked_oracle_onramp`
- Attacker controls: replay of a previously valid mode-switch layout
- Exploit idea: Probe account-key validation where type or owner may be checked but exact configured identity is not. Focus specifically on exact oracle identity and lineage, not merely owner/type compatibility.
- Invariant to test: staked onramp mode transitions must be strictly authorized and leave no mixed pricing state for live collateral
- Expected Immunefi impact: High: live collateral mispricing or durable user freeze
- Fast validation: Substitute same-type oracle-related accounts and assert every non-canonical identity is rejected. Substitute same-type oracle-related accounts and assert every non-canonical identity is rejected even if shape-compatible.
