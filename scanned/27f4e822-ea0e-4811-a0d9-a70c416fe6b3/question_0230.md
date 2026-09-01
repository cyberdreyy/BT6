# Q0230: Shares minted for zero NEAR - 1-yocto amount

## Question
Can an unprivileged attacker reach a state where `num_shares` is positive while `charge_amount` rounds to zero, with `amount = 1` yoctoNEAR so every U256 division truncates, breaking the invariant that no shares are ever minted for zero NEAR charged, and leading to direct theft of delegator NEAR from the staking pool?

## Target
- File/function: `staking-pool/src/internal.rs` - `num_shares_from_staked_amount_* / staked_amount_from_num_shares_*`
- Entrypoint: every value-moving pool method routes through these four helpers
- Attacker controls: the amounts fed into the helpers and the pool state at the time
- Exploit idea: Reach a state where `num_shares` is positive while `charge_amount` rounds to zero, with `amount = 1` yoctoNEAR so every U256 division truncates.
- Invariant to test: No shares are ever minted for zero NEAR charged.
- Expected Immunefi impact: Critical - direct theft of delegator NEAR from the staking pool.
- Fast validation: Unit test the boundary where charge rounds to zero.
