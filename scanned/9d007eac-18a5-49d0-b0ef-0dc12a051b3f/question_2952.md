# Q2952: Num_confirmations set to zero or one - receiver_id = outside

## Question
Can an unprivileged attacker reach a state where the stored threshold is degenerate and one call executes a request, with `receiver_id` set to an account other than the multisig itself, breaking the invariant that the threshold is always at least two for a shared account, and leading to unauthorized execution of a multisig request that moves account funds?

## Target
- File/function: `multisig/src/lib.rs` - `MultiSigContract::add_request / confirm / execute_request (key-based v1)`
- Entrypoint: `add_request` / `confirm` require `predecessor == current_account_id`, i.e. any access key on the multisig account
- Attacker controls: which key signs, the request contents, and the timing around key changes
- Exploit idea: Reach a state where the stored threshold is degenerate and one call executes a request, with `receiver_id` set to an account other than the multisig itself.
- Invariant to test: The threshold is always at least two for a shared account.
- Expected Immunefi impact: Critical - unauthorized execution of a multisig request that moves account funds.
- Fast validation: Sim a `SetNumConfirmations` request.
