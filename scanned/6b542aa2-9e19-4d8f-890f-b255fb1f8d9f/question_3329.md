# Q3329: enable_staked_oracle_onramp: oracle-config auth bypass installs attacker-chosen pricing [an-attacker-signer-targeting-a] [downstream-cache]

## Question
Can an unprivileged attacker invoke `enable_staked_oracle_onramp` with an attacker signer targeting a group with live staked banks so `enable_staked_oracle_onramp` installs or switches to attacker-chosen pricing, violating `staked onramp mode transitions must be strictly authorized and leave no mixed pricing state for live collateral` and causing `High: live collateral mispricing or durable user freeze`? Focus specifically on the downstream cache and mode assumptions that a protected pricing change would affect.

## Target
- File/function: `programs/marginfi/src/instructions/marginfi_group/on_ramp_transition.rs` / `enable_staked_oracle_onramp`
- Entrypoint: `enable_staked_oracle_onramp`
- Attacker controls: an attacker signer targeting a group with live staked banks
- Exploit idea: Oracle and fixed-price config is fully in scope when a public path can reach it without the intended role. Focus specifically on the downstream cache and mode assumptions that a protected pricing change would affect.
- Invariant to test: staked onramp mode transitions must be strictly authorized and leave no mixed pricing state for live collateral
- Expected Immunefi impact: High: live collateral mispricing or durable user freeze
- Fast validation: Attempt attacker-authored pricing reconfiguration and assert no oracle/fixed-price field changes without exact authorized signatures. After the attempted config mutation, immediately run the dependent public cache or user action and assert no inconsistent mode remains.
