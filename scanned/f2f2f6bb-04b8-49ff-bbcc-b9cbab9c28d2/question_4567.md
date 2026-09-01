# Q4567: Confirmations map desynchronised from requests - receiver_id = outside

## Question
Can an unprivileged attacker reach a state where `assert_valid_request`'s existence checks pass while the two maps describe different requests, with `receiver_id` set to an account other than the multisig itself, breaking the invariant that the requests map and the confirmations map always agree, and leading to unauthorized execution of a multisig request that moves account funds?

## Target
- File/function: `multisig2/src/lib.rs` - `MultiSigContract::confirm / assert_valid_request / current_member`
- Entrypoint: `confirm(request_id)` - reachable by any predecessor the member set matches, and by the account itself through any of its access keys
- Attacker controls: the request id, the identity `current_member()` derives, and the timing relative to membership changes
- Exploit idea: Reach a state where `assert_valid_request`'s existence checks pass while the two maps describe different requests, with `receiver_id` set to an account other than the multisig itself.
- Invariant to test: The requests map and the confirmations map always agree.
- Expected Immunefi impact: Critical - unauthorized execution of a multisig request that moves account funds.
- Fast validation: Sim the divergence and attempt execution.
