# Q0020: Stale confirmation counted after removal - cooldown boundary

## Question
Can an unprivileged attacker have `delete_member` remove a member while their confirmations on other requests stay in the `confirmations` map, so the threshold is met by principals who are no longer members, at exactly `added_timestamp + REQUEST_COOLDOWN`, breaking the invariant that an executed request is confirmed by `num_confirmations` members who are members at execution time, and leading to unauthorized execution of a multisig request that moves account funds?

## Target
- File/function: `multisig2/src/lib.rs` - `MultiSigContract::confirm / assert_valid_request / current_member`
- Entrypoint: `confirm(request_id)` - reachable by any predecessor the member set matches, and by the account itself through any of its access keys
- Attacker controls: the request id, the identity `current_member()` derives, and the timing relative to membership changes
- Exploit idea: Have `delete_member` remove a member while their confirmations on other requests stay in the `confirmations` map, so the threshold is met by principals who are no longer members, at exactly `added_timestamp + REQUEST_COOLDOWN`.
- Invariant to test: An executed request is confirmed by `num_confirmations` members who are members at execution time.
- Expected Immunefi impact: Critical - unauthorized execution of a multisig request that moves account funds.
- Fast validation: Sim confirm, remove the confirmer, then confirm once more and observe execution.
