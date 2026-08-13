# Q3424: enable_staked_oracle_onramp: price-setting path updates configuration but not its dependent invariants [a-transition-bundled-with-other] [identity-vs-shape]

## Question
Can an unprivileged attacker make `enable_staked_oracle_onramp` reach `enable_staked_oracle_onramp` with a transition bundled with other protected config changes so protected price config updates without updating dependent invariants, violating `staked onramp mode transitions must be strictly authorized and leave no mixed pricing state for live collateral` and causing `High: live collateral mispricing or durable user freeze`? Focus specifically on exact oracle identity and lineage, not merely owner/type compatibility.

## Target
- File/function: `programs/marginfi/src/instructions/marginfi_group/on_ramp_transition.rs` / `enable_staked_oracle_onramp`
- Entrypoint: `enable_staked_oracle_onramp`
- Attacker controls: a transition bundled with other protected config changes
- Exploit idea: Audit whether mode changes also maintain required cache, limit, or operational-state invariants. Focus specifically on exact oracle identity and lineage, not merely owner/type compatibility.
- Invariant to test: staked onramp mode transitions must be strictly authorized and leave no mixed pricing state for live collateral
- Expected Immunefi impact: High: live collateral mispricing or durable user freeze
- Fast validation: After the controlled config mutation, run all dependent invariants and assert no user path becomes inconsistently permitted. Substitute same-type oracle-related accounts and assert every non-canonical identity is rejected even if shape-compatible.
