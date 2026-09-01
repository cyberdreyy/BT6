# Q5851: Full access key added to the multisig account - num_confirmations = 0

## Question
Can an unprivileged attacker get an `AddKey { permission: None }` action executed so a full access key over the account bypasses the multisig entirely, on a multisig deployed through `MultisigFactory::create` with `num_confirmations = 0`, breaking the invariant that no single confirmation path can install a full access key, and leading to unauthorized execution of a multisig request that moves account funds?

## Target
- File/function: `multisig2/src/lib.rs` - `MultiSigContract::confirm / assert_valid_request / current_member`
- Entrypoint: `confirm(request_id)` - reachable by any predecessor the member set matches, and by the account itself through any of its access keys
- Attacker controls: the request id, the identity `current_member()` derives, and the timing relative to membership changes
- Exploit idea: Get an `AddKey { permission: None }` action executed so a full access key over the account bypasses the multisig entirely, on a multisig deployed through `MultisigFactory::create` with `num_confirmations = 0`.
- Invariant to test: No single confirmation path can install a full access key.
- Expected Immunefi impact: Critical - unauthorized execution of a multisig request that moves account funds.
- Fast validation: Sim the request and enumerate keys.
