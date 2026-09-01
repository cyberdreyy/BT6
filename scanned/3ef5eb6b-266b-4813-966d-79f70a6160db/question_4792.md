# Q4792: Predecessor equals the contract account - threshold lowered in batch

## Question
Can an unprivileged attacker call from the multisig account itself so `current_member()` takes the `signer_account_pk` branch and derives a member identity from the signing key rather than the caller, in a request that also carries `SetNumConfirmations { num_confirmations: 1 }`, breaking the invariant that the confirming identity is the principal that authorised the call, and leading to unauthorized execution of a multisig request that moves account funds?

## Target
- File/function: `multisig2/src/lib.rs` - `MultiSigContract::confirm / assert_valid_request / current_member`
- Entrypoint: `confirm(request_id)` - reachable by any predecessor the member set matches, and by the account itself through any of its access keys
- Attacker controls: the request id, the identity `current_member()` derives, and the timing relative to membership changes
- Exploit idea: Call from the multisig account itself so `current_member()` takes the `signer_account_pk` branch and derives a member identity from the signing key rather than the caller, in a request that also carries `SetNumConfirmations { num_confirmations: 1 }`.
- Invariant to test: The confirming identity is the principal that authorised the call.
- Expected Immunefi impact: Critical - unauthorized execution of a multisig request that moves account funds.
- Fast validation: Sim a self-call with a key that is not a member.
