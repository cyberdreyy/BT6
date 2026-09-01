# Q3215: Function call action with attached deposit - nonce driven high

## Question
Can an unprivileged attacker get a `FunctionCall` action executed carrying a deposit, moving NEAR to an attacker contract under the guise of a call, after `request_nonce` was driven toward `u32::MAX` by repeated `add_request` calls, breaking the invariant that value attached to executed actions is part of what members approved, and leading to unauthorized execution of a multisig request that moves account funds?

## Target
- File/function: `multisig2/src/lib.rs` - `MultiSigContract::confirm / assert_valid_request / current_member`
- Entrypoint: `confirm(request_id)` - reachable by any predecessor the member set matches, and by the account itself through any of its access keys
- Attacker controls: the request id, the identity `current_member()` derives, and the timing relative to membership changes
- Exploit idea: Get a `FunctionCall` action executed carrying a deposit, moving NEAR to an attacker contract under the guise of a call, after `request_nonce` was driven toward `u32::MAX` by repeated `add_request` calls.
- Invariant to test: Value attached to executed actions is part of what members approved.
- Expected Immunefi impact: Critical - unauthorized execution of a multisig request that moves account funds.
- Fast validation: Sim the action and track the NEAR.
