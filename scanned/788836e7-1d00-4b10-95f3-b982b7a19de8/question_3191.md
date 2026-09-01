# Q3191: Deploy contract action over the multisig account - nonce driven high

## Question
Can an unprivileged attacker get a `DeployContract` action executed against the multisig account so its own logic is replaced, after `request_nonce` was driven toward `u32::MAX` by repeated `add_request` calls, breaking the invariant that the multisig's code changes only through a fully confirmed request, and leading to unauthorized execution of a multisig request that moves account funds?

## Target
- File/function: `multisig2/src/lib.rs` - `MultiSigContract::confirm / assert_valid_request / current_member`
- Entrypoint: `confirm(request_id)` - reachable by any predecessor the member set matches, and by the account itself through any of its access keys
- Attacker controls: the request id, the identity `current_member()` derives, and the timing relative to membership changes
- Exploit idea: Get a `DeployContract` action executed against the multisig account so its own logic is replaced, after `request_nonce` was driven toward `u32::MAX` by repeated `add_request` calls.
- Invariant to test: The multisig's code changes only through a fully confirmed request.
- Expected Immunefi impact: Critical - unauthorized execution of a multisig request that moves account funds.
- Fast validation: Sim the action and compare code hashes.
