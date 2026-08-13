# Q3346: enable_staked_oracle_onramp: oracle-config path binds the wrong bank or group [an-attacker-signer-targeting-a] [identity-vs-shape]

## Question
Can an unprivileged attacker supply an attacker signer targeting a group with live staked banks to `enable_staked_oracle_onramp` so `enable_staked_oracle_onramp` reconfigures the wrong bank/group oracle context, violating `staked onramp mode transitions must be strictly authorized and leave no mixed pricing state for live collateral` and causing `High: live collateral mispricing or durable user freeze`? Focus specifically on exact oracle identity and lineage, not merely owner/type compatibility.

## Target
- File/function: `programs/marginfi/src/instructions/marginfi_group/on_ramp_transition.rs` / `enable_staked_oracle_onramp`
- Entrypoint: `enable_staked_oracle_onramp`
- Attacker controls: an attacker signer targeting a group with live staked banks
- Exploit idea: Probe whether bank/group binding is enforced as tightly as signer authorization on pricing config paths. Focus specifically on exact oracle identity and lineage, not merely owner/type compatibility.
- Invariant to test: staked onramp mode transitions must be strictly authorized and leave no mixed pricing state for live collateral
- Expected Immunefi impact: High: live collateral mispricing or durable user freeze
- Fast validation: Mix same-group and cross-group banks and assert pricing config changes can only land on the exact validated bank. Substitute same-type oracle-related accounts and assert every non-canonical identity is rejected even if shape-compatible.
