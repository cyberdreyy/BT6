# Q3415: enable_staked_oracle_onramp: price-setting path updates configuration but not its dependent invariants [groups-with-mixed-staked-and] [downstream-cache]

## Question
Can an unprivileged attacker make `enable_staked_oracle_onramp` reach `enable_staked_oracle_onramp` with groups with mixed staked and non-staked banks in adjacent state so protected price config updates without updating dependent invariants, violating `staked onramp mode transitions must be strictly authorized and leave no mixed pricing state for live collateral` and causing `High: live collateral mispricing or durable user freeze`? Focus specifically on the downstream cache and mode assumptions that a protected pricing change would affect.

## Target
- File/function: `programs/marginfi/src/instructions/marginfi_group/on_ramp_transition.rs` / `enable_staked_oracle_onramp`
- Entrypoint: `enable_staked_oracle_onramp`
- Attacker controls: groups with mixed staked and non-staked banks in adjacent state
- Exploit idea: Audit whether mode changes also maintain required cache, limit, or operational-state invariants. Focus specifically on the downstream cache and mode assumptions that a protected pricing change would affect.
- Invariant to test: staked onramp mode transitions must be strictly authorized and leave no mixed pricing state for live collateral
- Expected Immunefi impact: High: live collateral mispricing or durable user freeze
- Fast validation: After the controlled config mutation, run all dependent invariants and assert no user path becomes inconsistently permitted. After the attempted config mutation, immediately run the dependent public cache or user action and assert no inconsistent mode remains.
