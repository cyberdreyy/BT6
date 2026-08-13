# Q3447: enable_staked_oracle_onramp: oracle-config path reuses stale auxiliary state from a previous mode [groups-with-mixed-staked-and] [downstream-cache]

## Question
Can an unprivileged attacker route `enable_staked_oracle_onramp` through `enable_staked_oracle_onramp` with groups with mixed staked and non-staked banks in adjacent state so the bank reuses stale auxiliary state from a previous oracle mode, violating `staked onramp mode transitions must be strictly authorized and leave no mixed pricing state for live collateral` and causing `High: live collateral mispricing or durable user freeze`? Focus specifically on the downstream cache and mode assumptions that a protected pricing change would affect.

## Target
- File/function: `programs/marginfi/src/instructions/marginfi_group/on_ramp_transition.rs` / `enable_staked_oracle_onramp`
- Entrypoint: `enable_staked_oracle_onramp`
- Attacker controls: groups with mixed staked and non-staked banks in adjacent state
- Exploit idea: Mode transitions must not leave old cached assumptions live if a public bug can trigger them. Focus specifically on the downstream cache and mode assumptions that a protected pricing change would affect.
- Invariant to test: staked onramp mode transitions must be strictly authorized and leave no mixed pricing state for live collateral
- Expected Immunefi impact: High: live collateral mispricing or durable user freeze
- Fast validation: Switch modes in adversarial sequences and assert downstream pricing always reflects the final mode only. After the attempted config mutation, immediately run the dependent public cache or user action and assert no inconsistent mode remains.
