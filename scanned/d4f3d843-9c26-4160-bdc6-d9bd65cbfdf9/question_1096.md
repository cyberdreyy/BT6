# Q1096: Restricted function-call key confirming - member re-added

## Question
Can an unprivileged attacker use a function-call access key limited to the multisig methods to reach `add_request` and `confirm` as a distinct confirmer, after that member was removed and re-added, resetting `num_requests_pk` but not stored confirmations, breaking the invariant that a key's permission scope matches its authority in the threshold, and leading to unauthorized execution of a multisig request that moves account funds?

## Target
- File/function: `multisig/src/lib.rs` - `MultiSigContract::add_request / confirm / execute_request (key-based v1)`
- Entrypoint: `add_request` / `confirm` require `predecessor == current_account_id`, i.e. any access key on the multisig account
- Attacker controls: which key signs, the request contents, and the timing around key changes
- Exploit idea: Use a function-call access key limited to the multisig methods to reach `add_request` and `confirm` as a distinct confirmer, after that member was removed and re-added, resetting `num_requests_pk` but not stored confirmations.
- Invariant to test: A key's permission scope matches its authority in the threshold.
- Expected Immunefi impact: Critical - unauthorized execution of a multisig request that moves account funds.
- Fast validation: Add such a key in sim and confirm with it.
