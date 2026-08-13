# Q3434: enable_staked_oracle_onramp: oracle-config path can retarget future permissionless cache writes [replay-of-a-previously-valid] [identity-vs-shape]

## Question
Can an unprivileged attacker use `enable_staked_oracle_onramp` with replay of a previously valid mode-switch layout so `enable_staked_oracle_onramp` retargets future permissionless cache writes to attacker-selected pricing context, violating `staked onramp mode transitions must be strictly authorized and leave no mixed pricing state for live collateral` and leading to `High: live collateral mispricing or durable user freeze`? Focus specifically on exact oracle identity and lineage, not merely owner/type compatibility.

## Target
- File/function: `programs/marginfi/src/instructions/marginfi_group/on_ramp_transition.rs` / `enable_staked_oracle_onramp`
- Entrypoint: `enable_staked_oracle_onramp`
- Attacker controls: replay of a previously valid mode-switch layout
- Exploit idea: Check whether protected config fields later consumed by permissionless cache-refresh paths can be corrupted by a public auth/binding bug. Focus specifically on exact oracle identity and lineage, not merely owner/type compatibility.
- Invariant to test: staked onramp mode transitions must be strictly authorized and leave no mixed pricing state for live collateral
- Expected Immunefi impact: High: live collateral mispricing or durable user freeze
- Fast validation: Mutate the config under attacker conditions, then try the permissionless refresher and assert it still cannot write from attacker-selected sources. Substitute same-type oracle-related accounts and assert every non-canonical identity is rejected even if shape-compatible.
