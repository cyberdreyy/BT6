# Q3379: enable_staked_oracle_onramp: oracle-config validation checks shape but not exact key lineage [same-slot-mode-switch-followed] [downstream-cache]

## Question
Can an unprivileged attacker use `enable_staked_oracle_onramp` with same-slot mode-switch followed by price-cache pulse or borrow investigation path so `enable_staked_oracle_onramp` accepts an oracle-related account with the right shape but wrong lineage, violating `staked onramp mode transitions must be strictly authorized and leave no mixed pricing state for live collateral` and causing `High: live collateral mispricing or durable user freeze`? Focus specifically on the downstream cache and mode assumptions that a protected pricing change would affect.

## Target
- File/function: `programs/marginfi/src/instructions/marginfi_group/on_ramp_transition.rs` / `enable_staked_oracle_onramp`
- Entrypoint: `enable_staked_oracle_onramp`
- Attacker controls: same-slot mode-switch followed by price-cache pulse or borrow investigation path
- Exploit idea: Probe account-key validation where type or owner may be checked but exact configured identity is not. Focus specifically on the downstream cache and mode assumptions that a protected pricing change would affect.
- Invariant to test: staked onramp mode transitions must be strictly authorized and leave no mixed pricing state for live collateral
- Expected Immunefi impact: High: live collateral mispricing or durable user freeze
- Fast validation: Substitute same-type oracle-related accounts and assert every non-canonical identity is rejected. After the attempted config mutation, immediately run the dependent public cache or user action and assert no inconsistent mode remains.
