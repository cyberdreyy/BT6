# Q0781: Row rewritten by a concurrent receipt - first receipt of an epoch

## Question
Can an unprivileged attacker have two receipts in one block both read and write the same row, so one write is lost, in the first receipt of a new epoch, before any other account triggers `internal_ping`, breaking the invariant that concurrent updates to one row never lose a credit or a debit, and leading to direct theft of delegator NEAR from the staking pool?

## Target
- File/function: `staking-pool/src/internal.rs` - `internal_get_account / internal_save_account / get_account`
- Entrypoint: any delegator method, plus the public view methods `get_account`, `get_accounts`, `get_number_of_accounts`
- Attacker controls: when its row exists, when it is deleted, and what an integrator reads from the views
- Exploit idea: Have two receipts in one block both read and write the same row, so one write is lost, in the first receipt of a new epoch, before any other account triggers `internal_ping`.
- Invariant to test: Concurrent updates to one row never lose a credit or a debit.
- Expected Immunefi impact: Critical - direct theft of delegator NEAR from the staking pool.
- Fast validation: Sim two same-block actions and reconcile the row.
