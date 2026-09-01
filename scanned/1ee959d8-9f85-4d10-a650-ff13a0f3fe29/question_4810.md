# Q4810: Confirmation replayed after re-adding a member - threshold lowered in batch

## Question
Can an unprivileged attacker remove and re-add a member so their stored confirmation is reusable while `num_requests_pk` was reset, in a request that also carries `SetNumConfirmations { num_confirmations: 1 }`, breaking the invariant that one member contributes at most one confirmation per request, and leading to unauthorized execution of a multisig request that moves account funds?

## Target
- File/function: `multisig2/src/lib.rs` - `MultiSigContract::confirm / assert_valid_request / current_member`
- Entrypoint: `confirm(request_id)` - reachable by any predecessor the member set matches, and by the account itself through any of its access keys
- Attacker controls: the request id, the identity `current_member()` derives, and the timing relative to membership changes
- Exploit idea: Remove and re-add a member so their stored confirmation is reusable while `num_requests_pk` was reset, in a request that also carries `SetNumConfirmations { num_confirmations: 1 }`.
- Invariant to test: One member contributes at most one confirmation per request.
- Expected Immunefi impact: Critical - unauthorized execution of a multisig request that moves account funds.
- Fast validation: Sim remove/re-add and count confirmations.
