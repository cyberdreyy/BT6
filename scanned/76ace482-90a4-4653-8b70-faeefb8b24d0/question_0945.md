# Q0945: Num_requests_pk underflow accounting - one short of threshold

## Question
Can an unprivileged attacker exploit the guarded decrement in `remove_request` so a member's counter drifts and their limit is effectively removed or permanent, when the request already carries `num_confirmations - 1` confirmations, breaking the invariant that the counter equals the member's live pending requests, and leading to protocol accounting value diverges from reality and another party settles on it?

## Target
- File/function: `multisig2/src/lib.rs` - `MultiSigContract::confirm / assert_valid_request / current_member`
- Entrypoint: `confirm(request_id)` - reachable by any predecessor the member set matches, and by the account itself through any of its access keys
- Attacker controls: the request id, the identity `current_member()` derives, and the timing relative to membership changes
- Exploit idea: Exploit the guarded decrement in `remove_request` so a member's counter drifts and their limit is effectively removed or permanent, when the request already carries `num_confirmations - 1` confirmations.
- Invariant to test: The counter equals the member's live pending requests.
- Expected Immunefi impact: High - protocol accounting value diverges from reality and another party settles on it.
- Fast validation: Sim churn and compare the counter.
