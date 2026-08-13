# Q3376: enable_staked_oracle_onramp: price-cache or fixed-price mode switch leaves unsafe mixed state [a-transition-bundled-with-other] [identity-vs-shape]

## Question
Can an unprivileged attacker make `enable_staked_oracle_onramp` reach `enable_staked_oracle_onramp` with a transition bundled with other protected config changes so a price mode switch leaves unsafe mixed state, violating `staked onramp mode transitions must be strictly authorized and leave no mixed pricing state for live collateral` and leading to `High: live collateral mispricing or durable user freeze`? Focus specifically on exact oracle identity and lineage, not merely owner/type compatibility.

## Target
- File/function: `programs/marginfi/src/instructions/marginfi_group/on_ramp_transition.rs` / `enable_staked_oracle_onramp`
- Entrypoint: `enable_staked_oracle_onramp`
- Attacker controls: a transition bundled with other protected config changes
- Exploit idea: Check whether switching oracle modes or staked onramp settings fully invalidates or refreshes dependent cached state. Focus specifically on exact oracle identity and lineage, not merely owner/type compatibility.
- Invariant to test: staked onramp mode transitions must be strictly authorized and leave no mixed pricing state for live collateral
- Expected Immunefi impact: High: live collateral mispricing or durable user freeze
- Fast validation: Perform the controlled switch, then immediately run dependent user actions and assert they see a single coherent pricing mode. Substitute same-type oracle-related accounts and assert every non-canonical identity is rejected even if shape-compatible.
