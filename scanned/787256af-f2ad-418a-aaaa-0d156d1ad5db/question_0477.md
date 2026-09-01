# Q0477: Unlock clock reset for existing unstaked NEAR - dust loop

## Question
Can an unprivileged attacker unstake a dust amount so `unstaked_available_epoch_height` is pushed forward for a balance that was already withdrawable, repeating the call thousands of times with dust amounts inside one epoch, breaking the invariant that an already-matured `unstaked` balance stays withdrawable regardless of later unstakes, and leading to temporary freezing of user funds for at least four epochs?

## Target
- File/function: `staking-pool/src/internal.rs` - `StakingContract::inner_unstake`
- Entrypoint: `unstake(amount)` / `unstake_all()` - any delegator
- Attacker controls: the unstaked amount, timing relative to `internal_ping`, and the number of repetitions
- Exploit idea: Unstake a dust amount so `unstaked_available_epoch_height` is pushed forward for a balance that was already withdrawable, repeating the call thousands of times with dust amounts inside one epoch.
- Invariant to test: An already-matured `unstaked` balance stays withdrawable regardless of later unstakes.
- Expected Immunefi impact: High - temporary freezing of user funds for at least four epochs.
- Fast validation: Sim: mature a balance, unstake 1 yocto, assert `can_withdraw` is unchanged.
