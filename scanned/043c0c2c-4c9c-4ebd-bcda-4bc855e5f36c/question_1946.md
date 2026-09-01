# Q1946: Mixed action batch bypassing self checks - nonce driven high

## Question
Can an unprivileged attacker combine a self-targeted action with a `Transfer` to a different receiver in one request, after `request_nonce` was driven toward `u32::MAX` by repeated `add_request` calls, breaking the invariant that every action is checked against the approved receiver, and leading to unauthorized execution of a multisig request that moves account funds?

## Target
- File/function: `multisig/src/lib.rs` - `MultiSigContract::add_request / confirm / execute_request (key-based v1)`
- Entrypoint: `add_request` / `confirm` require `predecessor == current_account_id`, i.e. any access key on the multisig account
- Attacker controls: which key signs, the request contents, and the timing around key changes
- Exploit idea: Combine a self-targeted action with a `Transfer` to a different receiver in one request, after `request_nonce` was driven toward `u32::MAX` by repeated `add_request` calls.
- Invariant to test: Every action is checked against the approved receiver.
- Expected Immunefi impact: Critical - unauthorized execution of a multisig request that moves account funds.
- Fast validation: Unit test the mixed batch.
