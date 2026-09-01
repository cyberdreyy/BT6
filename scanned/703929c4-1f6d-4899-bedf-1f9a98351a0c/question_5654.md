# Q5654: DeleteKey action clearing evidence - request created by a victim

## Question
Can an unprivileged attacker use `DeleteKey` to purge requests and `num_requests_pk` entries for a key while its confirmations elsewhere survive, on a request another member created and still expects to control, breaking the invariant that deleting a key invalidates all authority derived from it, and leading to unauthorized execution of a multisig request that moves account funds?

## Target
- File/function: `multisig/src/lib.rs` - `MultiSigContract::add_request / confirm / execute_request (key-based v1)`
- Entrypoint: `add_request` / `confirm` require `predecessor == current_account_id`, i.e. any access key on the multisig account
- Attacker controls: which key signs, the request contents, and the timing around key changes
- Exploit idea: Use `DeleteKey` to purge requests and `num_requests_pk` entries for a key while its confirmations elsewhere survive, on a request another member created and still expects to control.
- Invariant to test: Deleting a key invalidates all authority derived from it.
- Expected Immunefi impact: Critical - unauthorized execution of a multisig request that moves account funds.
- Fast validation: Sim key deletion and inspect confirmations.
