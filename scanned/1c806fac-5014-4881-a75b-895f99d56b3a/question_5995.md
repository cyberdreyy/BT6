# Q5995: Value action smuggled past assert_self_request - members below threshold

## Question
Can an unprivileged attacker build an `actions` vector where a self-targeted action satisfies `assert_self_request` while another action in the same request moves value to a different receiver, on a multisig whose member set has fallen below `num_confirmations`, breaking the invariant that every action in a request is checked against the receiver the members approved, and leading to unauthorized execution of a multisig request that moves account funds?

## Target
- File/function: `multisig2/src/lib.rs` - `MultiSigContract::confirm / assert_valid_request / current_member`
- Entrypoint: `confirm(request_id)` - reachable by any predecessor the member set matches, and by the account itself through any of its access keys
- Attacker controls: the request id, the identity `current_member()` derives, and the timing relative to membership changes
- Exploit idea: Build an `actions` vector where a self-targeted action satisfies `assert_self_request` while another action in the same request moves value to a different receiver, on a multisig whose member set has fallen below `num_confirmations`.
- Invariant to test: Every action in a request is checked against the receiver the members approved.
- Expected Immunefi impact: Critical - unauthorized execution of a multisig request that moves account funds.
- Fast validation: Unit test a mixed action vector.
