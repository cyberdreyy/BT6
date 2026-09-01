# Q1095: Execution ordering inside one request - one short of threshold

## Question
Can an unprivileged attacker order actions so a state-mutating action changes the checks applied to a later action in the same request, when the request already carries `num_confirmations - 1` confirmations, breaking the invariant that each action's checks are evaluated against the state the members approved, and leading to unauthorized execution of a multisig request that moves account funds?

## Target
- File/function: `multisig2/src/lib.rs` - `MultiSigContract::confirm / assert_valid_request / current_member`
- Entrypoint: `confirm(request_id)` - reachable by any predecessor the member set matches, and by the account itself through any of its access keys
- Attacker controls: the request id, the identity `current_member()` derives, and the timing relative to membership changes
- Exploit idea: Order actions so a state-mutating action changes the checks applied to a later action in the same request, when the request already carries `num_confirmations - 1` confirmations.
- Invariant to test: Each action's checks are evaluated against the state the members approved.
- Expected Immunefi impact: Critical - unauthorized execution of a multisig request that moves account funds.
- Fast validation: Unit test ordered action vectors.
