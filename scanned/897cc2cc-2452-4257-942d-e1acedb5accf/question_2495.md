# Q2495: Request nonce reuse - request limit reached

## Question
Can an unprivileged attacker drive `request_nonce` so a new request lands on an id whose confirmations from an older request still exist, while the attacker's `num_requests_pk` entry sits at `active_requests_limit`, breaking the invariant that a request id is never reused while stale confirmations exist, and leading to unauthorized execution of a multisig request that moves account funds?

## Target
- File/function: `multisig2/src/lib.rs` - `MultiSigContract::confirm / assert_valid_request / current_member`
- Entrypoint: `confirm(request_id)` - reachable by any predecessor the member set matches, and by the account itself through any of its access keys
- Attacker controls: the request id, the identity `current_member()` derives, and the timing relative to membership changes
- Exploit idea: Drive `request_nonce` so a new request lands on an id whose confirmations from an older request still exist, while the attacker's `num_requests_pk` entry sits at `active_requests_limit`.
- Invariant to test: A request id is never reused while stale confirmations exist.
- Expected Immunefi impact: Critical - unauthorized execution of a multisig request that moves account funds.
- Fast validation: Sim nonce progression and inspect the maps.
