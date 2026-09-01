# Q0206: Row deletion loses the unlock deadline - dust loop

## Question
Can an unprivileged attacker zero out both balances so `internal_save_account` deletes the row, discarding `unstaked_available_epoch_height`, then deposit again, repeating the call thousands of times with dust amounts inside one epoch, breaking the invariant that the unlock deadline for unstaked NEAR survives any row deletion, and leading to direct theft of delegator NEAR from the staking pool?

## Target
- File/function: `staking-pool/src/internal.rs` - `internal_get_account / internal_save_account / get_account`
- Entrypoint: any delegator method, plus the public view methods `get_account`, `get_accounts`, `get_number_of_accounts`
- Attacker controls: when its row exists, when it is deleted, and what an integrator reads from the views
- Exploit idea: Zero out both balances so `internal_save_account` deletes the row, discarding `unstaked_available_epoch_height`, then deposit again, repeating the call thousands of times with dust amounts inside one epoch.
- Invariant to test: The unlock deadline for unstaked NEAR survives any row deletion.
- Expected Immunefi impact: Critical - direct theft of delegator NEAR from the staking pool.
- Fast validation: Sim delete/recreate and assert `can_withdraw` stays false.
