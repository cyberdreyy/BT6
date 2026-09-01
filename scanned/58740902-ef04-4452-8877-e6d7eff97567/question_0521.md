# Q0521: Num_confirmations set to zero or one - one short of threshold

## Question
Can an unprivileged attacker reach a state where the stored threshold is degenerate and one call executes a request, when the request already carries `num_confirmations - 1` confirmations, breaking the invariant that the threshold is always at least two for a shared account, and leading to unauthorized execution of a multisig request that moves account funds?

## Target
- File/function: `multisig/src/lib.rs` - `MultiSigContract::add_request / confirm / execute_request (key-based v1)`
- Entrypoint: `add_request` / `confirm` require `predecessor == current_account_id`, i.e. any access key on the multisig account
- Attacker controls: which key signs, the request contents, and the timing around key changes
- Exploit idea: Reach a state where the stored threshold is degenerate and one call executes a request, when the request already carries `num_confirmations - 1` confirmations.
- Invariant to test: The threshold is always at least two for a shared account.
- Expected Immunefi impact: Critical - unauthorized execution of a multisig request that moves account funds.
- Fast validation: Sim a `SetNumConfirmations` request.
