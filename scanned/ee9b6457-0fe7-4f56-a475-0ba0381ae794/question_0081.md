# Q0081: Can_withdraw view vs the withdraw assertion - 1-yocto amount

## Question
Can an unprivileged attacker make `is_account_unstaked_balance_available` report true while `internal_withdraw`'s assertion still rejects the call, or the reverse, with `amount = 1` yoctoNEAR so every U256 division truncates, breaking the invariant that the view and the assertion agree for every account and epoch, and leading to protocol accounting value diverges from reality and another party settles on it?

## Target
- File/function: `staking-pool/src/internal.rs` - `internal_get_account / internal_save_account / get_account`
- Entrypoint: any delegator method, plus the public view methods `get_account`, `get_accounts`, `get_number_of_accounts`
- Attacker controls: when its row exists, when it is deleted, and what an integrator reads from the views
- Exploit idea: Make `is_account_unstaked_balance_available` report true while `internal_withdraw`'s assertion still rejects the call, or the reverse, with `amount = 1` yoctoNEAR so every U256 division truncates.
- Invariant to test: The view and the assertion agree for every account and epoch.
- Expected Immunefi impact: High - protocol accounting value diverges from reality and another party settles on it.
- Fast validation: Sim across epoch boundaries and compare.
