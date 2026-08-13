# Q3427: enable_staked_oracle_onramp: oracle-config path can retarget future permissionless cache writes [same-slot-mode-switch-followed] [downstream-cache]

## Question
Can an unprivileged attacker use `enable_staked_oracle_onramp` with same-slot mode-switch followed by price-cache pulse or borrow investigation path so `enable_staked_oracle_onramp` retargets future permissionless cache writes to attacker-selected pricing context, violating `staked onramp mode transitions must be strictly authorized and leave no mixed pricing state for live collateral` and leading to `High: live collateral mispricing or durable user freeze`? Focus specifically on the downstream cache and mode assumptions that a protected pricing change would affect.

## Target
- File/function: `programs/marginfi/src/instructions/marginfi_group/on_ramp_transition.rs` / `enable_staked_oracle_onramp`
- Entrypoint: `enable_staked_oracle_onramp`
- Attacker controls: same-slot mode-switch followed by price-cache pulse or borrow investigation path
- Exploit idea: Check whether protected config fields later consumed by permissionless cache-refresh paths can be corrupted by a public auth/binding bug. Focus specifically on the downstream cache and mode assumptions that a protected pricing change would affect.
- Invariant to test: staked onramp mode transitions must be strictly authorized and leave no mixed pricing state for live collateral
- Expected Immunefi impact: High: live collateral mispricing or durable user freeze
- Fast validation: Mutate the config under attacker conditions, then try the permissionless refresher and assert it still cannot write from attacker-selected sources. After the attempted config mutation, immediately run the dependent public cache or user action and assert no inconsistent mode remains.
