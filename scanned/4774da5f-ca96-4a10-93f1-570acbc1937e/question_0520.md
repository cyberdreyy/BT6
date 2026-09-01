# Q0520: Delete_member removing the wrong requests - cooldown boundary

## Question
Can an unprivileged attacker exploit the filter in `delete_member` matching requests by member equality so an unrelated member's requests are deleted or retained, at exactly `added_timestamp + REQUEST_COOLDOWN`, breaking the invariant that member deletion touches exactly that member's requests, and leading to protocol accounting value diverges from reality and another party settles on it?

## Target
- File/function: `multisig2/src/lib.rs` - `MultiSigContract::confirm / assert_valid_request / current_member`
- Entrypoint: `confirm(request_id)` - reachable by any predecessor the member set matches, and by the account itself through any of its access keys
- Attacker controls: the request id, the identity `current_member()` derives, and the timing relative to membership changes
- Exploit idea: Exploit the filter in `delete_member` matching requests by member equality so an unrelated member's requests are deleted or retained, at exactly `added_timestamp + REQUEST_COOLDOWN`.
- Invariant to test: Member deletion touches exactly that member's requests.
- Expected Immunefi impact: High - protocol accounting value diverges from reality and another party settles on it.
- Fast validation: Unit test the filter with mixed members.
