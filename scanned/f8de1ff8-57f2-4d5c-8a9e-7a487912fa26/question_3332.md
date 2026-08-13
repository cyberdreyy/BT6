# Q3332: enable_staked_oracle_onramp: oracle-config auth bypass installs attacker-chosen pricing [same-slot-mode-switch-followed] [identity-vs-shape]

## Question
Can an unprivileged attacker invoke `enable_staked_oracle_onramp` with same-slot mode-switch followed by price-cache pulse or borrow investigation path so `enable_staked_oracle_onramp` installs or switches to attacker-chosen pricing, violating `staked onramp mode transitions must be strictly authorized and leave no mixed pricing state for live collateral` and causing `High: live collateral mispricing or durable user freeze`? Focus specifically on exact oracle identity and lineage, not merely owner/type compatibility.

## Target
- File/function: `programs/marginfi/src/instructions/marginfi_group/on_ramp_transition.rs` / `enable_staked_oracle_onramp`
- Entrypoint: `enable_staked_oracle_onramp`
- Attacker controls: same-slot mode-switch followed by price-cache pulse or borrow investigation path
- Exploit idea: Oracle and fixed-price config is fully in scope when a public path can reach it without the intended role. Focus specifically on exact oracle identity and lineage, not merely owner/type compatibility.
- Invariant to test: staked onramp mode transitions must be strictly authorized and leave no mixed pricing state for live collateral
- Expected Immunefi impact: High: live collateral mispricing or durable user freeze
- Fast validation: Attempt attacker-authored pricing reconfiguration and assert no oracle/fixed-price field changes without exact authorized signatures. Substitute same-type oracle-related accounts and assert every non-canonical identity is rejected even if shape-compatible.
