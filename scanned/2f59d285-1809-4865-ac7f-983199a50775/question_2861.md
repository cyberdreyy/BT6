# Q2861: U256 -> u128 truncation in share conversion - amount = balance - 1

## Question
Can an unprivileged attacker pass an `amount` large enough that the `U256` product inside `num_shares_from_staked_amount_rounded_down` exceeds `u128::MAX` and `.as_u128()` silently truncates, with `amount` one yoctoNEAR below the attacker's own recorded balance, breaking the invariant that `num_shares_from_staked_amount_rounded_down(a)` is monotonic in `a` and never wraps, and leading to direct theft of delegator NEAR from the staking pool?

## Target
- File/function: `staking-pool/src/internal.rs` - `StakingContract::internal_stake`
- Entrypoint: `stake(amount)` / `stake_all()` / `deposit_and_stake()` - any account, no role required
- Attacker controls: the staked amount, the call ordering inside a block, its own `unstaked` balance, and how many times it repeats
- Exploit idea: Pass an `amount` large enough that the `U256` product inside `num_shares_from_staked_amount_rounded_down` exceeds `u128::MAX` and `.as_u128()` silently truncates, with `amount` one yoctoNEAR below the attacker's own recorded balance.
- Invariant to test: `num_shares_from_staked_amount_rounded_down(a)` is monotonic in `a` and never wraps.
- Expected Immunefi impact: Critical - direct theft of delegator NEAR from the staking pool.
- Fast validation: Unit-test the conversion helpers directly with `u128::MAX`-scale inputs and assert monotonicity.
