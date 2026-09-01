# Q2903: Execution proceeds although the promise fails - nonce driven high

## Question
Can an unprivileged attacker rely on `remove_request` deleting the request and confirmations before `execute_request`, so a failed execution destroys the authorisation without performing it, after `request_nonce` was driven toward `u32::MAX` by repeated `add_request` calls, breaking the invariant that a request is consumed only when its actions actually execute, and leading to unauthorized execution of a multisig request that moves account funds?

## Target
- File/function: `multisig2/src/lib.rs` - `MultiSigContract::confirm / assert_valid_request / current_member`
- Entrypoint: `confirm(request_id)` - reachable by any predecessor the member set matches, and by the account itself through any of its access keys
- Attacker controls: the request id, the identity `current_member()` derives, and the timing relative to membership changes
- Exploit idea: Rely on `remove_request` deleting the request and confirmations before `execute_request`, so a failed execution destroys the authorisation without performing it, after `request_nonce` was driven toward `u32::MAX` by repeated `add_request` calls.
- Invariant to test: A request is consumed only when its actions actually execute.
- Expected Immunefi impact: Critical - unauthorized execution of a multisig request that moves account funds.
- Fast validation: Sim a failing execution and inspect the request map.
