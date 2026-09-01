# Q4828: Request executed after its confirmations were dropped - threshold lowered in batch

## Question
Can an unprivileged attacker exploit `delete_member` removing requests and confirmations for one member while other requests keep confirmations pointing at a deleted request id, in a request that also carries `SetNumConfirmations { num_confirmations: 1 }`, breaking the invariant that confirmations always refer to a live request, and leading to unauthorized execution of a multisig request that moves account funds?

## Target
- File/function: `multisig2/src/lib.rs` - `MultiSigContract::confirm / assert_valid_request / current_member`
- Entrypoint: `confirm(request_id)` - reachable by any predecessor the member set matches, and by the account itself through any of its access keys
- Attacker controls: the request id, the identity `current_member()` derives, and the timing relative to membership changes
- Exploit idea: Exploit `delete_member` removing requests and confirmations for one member while other requests keep confirmations pointing at a deleted request id, in a request that also carries `SetNumConfirmations { num_confirmations: 1 }`.
- Invariant to test: Confirmations always refer to a live request.
- Expected Immunefi impact: Critical - unauthorized execution of a multisig request that moves account funds.
- Fast validation: Sim member deletion with cross-referenced requests.
