# Q1246: Mixed action batch bypassing self checks - member re-added

## Question
Can an unprivileged attacker combine a self-targeted action with a `Transfer` to a different receiver in one request, after that member was removed and re-added, resetting `num_requests_pk` but not stored confirmations, breaking the invariant that every action is checked against the approved receiver, and leading to unauthorized execution of a multisig request that moves account funds?

## Target
- File/function: `multisig/src/lib.rs` - `MultiSigContract::add_request / confirm / execute_request (key-based v1)`
- Entrypoint: `add_request` / `confirm` require `predecessor == current_account_id`, i.e. any access key on the multisig account
- Attacker controls: which key signs, the request contents, and the timing around key changes
- Exploit idea: Combine a self-targeted action with a `Transfer` to a different receiver in one request, after that member was removed and re-added, resetting `num_requests_pk` but not stored confirmations.
- Invariant to test: Every action is checked against the approved receiver.
- Expected Immunefi impact: Critical - unauthorized execution of a multisig request that moves account funds.
- Fast validation: Unit test the mixed batch.
