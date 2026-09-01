# Q2544: DeleteKey action clearing evidence - receiver_id = self

## Question
Can an unprivileged attacker use `DeleteKey` to purge requests and `num_requests_pk` entries for a key while its confirmations elsewhere survive, with `receiver_id` set to `env::current_account_id()` so `assert_self_request` passes, breaking the invariant that deleting a key invalidates all authority derived from it, and leading to unauthorized execution of a multisig request that moves account funds?

## Target
- File/function: `multisig/src/lib.rs` - `MultiSigContract::add_request / confirm / execute_request (key-based v1)`
- Entrypoint: `add_request` / `confirm` require `predecessor == current_account_id`, i.e. any access key on the multisig account
- Attacker controls: which key signs, the request contents, and the timing around key changes
- Exploit idea: Use `DeleteKey` to purge requests and `num_requests_pk` entries for a key while its confirmations elsewhere survive, with `receiver_id` set to `env::current_account_id()` so `assert_self_request` passes.
- Invariant to test: Deleting a key invalidates all authority derived from it.
- Expected Immunefi impact: Critical - unauthorized execution of a multisig request that moves account funds.
- Fast validation: Sim key deletion and inspect confirmations.
