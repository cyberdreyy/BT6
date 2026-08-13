# Q3403: enable_staked_oracle_onramp: staked-oracle transition can be used to brick live collateral paths [a-transition-where-cached-price] [downstream-cache]

## Question
Can an unprivileged attacker invoke `enable_staked_oracle_onramp` with a transition where cached price state from the old mode already exists so `enable_staked_oracle_onramp` performs a staked-oracle transition that bricks or misprices live collateral paths, violating `staked onramp mode transitions must be strictly authorized and leave no mixed pricing state for live collateral` and causing `High: live collateral mispricing or durable user freeze`? Focus specifically on the downstream cache and mode assumptions that a protected pricing change would affect.

## Target
- File/function: `programs/marginfi/src/instructions/marginfi_group/on_ramp_transition.rs` / `enable_staked_oracle_onramp`
- Entrypoint: `enable_staked_oracle_onramp`
- Attacker controls: a transition where cached price state from the old mode already exists
- Exploit idea: This is in scope when caused by a public bypass, not by an admin making a policy choice. Focus specifically on the downstream cache and mode assumptions that a protected pricing change would affect.
- Invariant to test: staked onramp mode transitions must be strictly authorized and leave no mixed pricing state for live collateral
- Expected Immunefi impact: High: live collateral mispricing or durable user freeze
- Fast validation: Exercise the transition under attacker-controlled auth/binding attempts and assert it cannot affect live banks without the intended role and full validation. After the attempted config mutation, immediately run the dependent public cache or user action and assert no inconsistent mode remains.
