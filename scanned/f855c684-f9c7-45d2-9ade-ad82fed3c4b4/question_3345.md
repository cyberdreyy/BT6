# Q3345: enable_staked_oracle_onramp: oracle-config path binds the wrong bank or group [an-attacker-signer-targeting-a] [downstream-cache]

## Question
Can an unprivileged attacker supply an attacker signer targeting a group with live staked banks to `enable_staked_oracle_onramp` so `enable_staked_oracle_onramp` reconfigures the wrong bank/group oracle context, violating `staked onramp mode transitions must be strictly authorized and leave no mixed pricing state for live collateral` and causing `High: live collateral mispricing or durable user freeze`? Focus specifically on the downstream cache and mode assumptions that a protected pricing change would affect.

## Target
- File/function: `programs/marginfi/src/instructions/marginfi_group/on_ramp_transition.rs` / `enable_staked_oracle_onramp`
- Entrypoint: `enable_staked_oracle_onramp`
- Attacker controls: an attacker signer targeting a group with live staked banks
- Exploit idea: Probe whether bank/group binding is enforced as tightly as signer authorization on pricing config paths. Focus specifically on the downstream cache and mode assumptions that a protected pricing change would affect.
- Invariant to test: staked onramp mode transitions must be strictly authorized and leave no mixed pricing state for live collateral
- Expected Immunefi impact: High: live collateral mispricing or durable user freeze
- Fast validation: Mix same-group and cross-group banks and assert pricing config changes can only land on the exact validated bank. After the attempted config mutation, immediately run the dependent public cache or user action and assert no inconsistent mode remains.
