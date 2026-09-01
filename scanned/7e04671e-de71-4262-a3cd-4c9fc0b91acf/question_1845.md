# Q1845: Threshold lowered by a batched action - member re-added

## Question
Can an unprivileged attacker get a request executed that carries `SetNumConfirmations` so subsequent requests need fewer confirmations than the members agreed, after that member was removed and re-added, resetting `num_requests_pk` but not stored confirmations, breaking the invariant that the threshold changes only through a request that met the old threshold, and leading to unauthorized execution of a multisig request that moves account funds?

## Target
- File/function: `multisig2/src/lib.rs` - `MultiSigContract::confirm / assert_valid_request / current_member`
- Entrypoint: `confirm(request_id)` - reachable by any predecessor the member set matches, and by the account itself through any of its access keys
- Attacker controls: the request id, the identity `current_member()` derives, and the timing relative to membership changes
- Exploit idea: Get a request executed that carries `SetNumConfirmations` so subsequent requests need fewer confirmations than the members agreed, after that member was removed and re-added, resetting `num_requests_pk` but not stored confirmations.
- Invariant to test: The threshold changes only through a request that met the old threshold.
- Expected Immunefi impact: Critical - unauthorized execution of a multisig request that moves account funds.
- Fast validation: Sim the batch and check the resulting threshold.
