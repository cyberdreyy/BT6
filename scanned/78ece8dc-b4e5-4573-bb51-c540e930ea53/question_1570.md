# Q1570: Deploy contract action over the multisig account - confirmer removed

## Question
Can an unprivileged attacker get a `DeployContract` action executed against the multisig account so its own logic is replaced, after the member who confirmed was removed by a `DeleteMember` request, breaking the invariant that the multisig's code changes only through a fully confirmed request, and leading to unauthorized execution of a multisig request that moves account funds?

## Target
- File/function: `multisig2/src/lib.rs` - `MultiSigContract::confirm / assert_valid_request / current_member`
- Entrypoint: `confirm(request_id)` - reachable by any predecessor the member set matches, and by the account itself through any of its access keys
- Attacker controls: the request id, the identity `current_member()` derives, and the timing relative to membership changes
- Exploit idea: Get a `DeployContract` action executed against the multisig account so its own logic is replaced, after the member who confirmed was removed by a `DeleteMember` request.
- Invariant to test: The multisig's code changes only through a fully confirmed request.
- Expected Immunefi impact: Critical - unauthorized execution of a multisig request that moves account funds.
- Fast validation: Sim the action and compare code hashes.
