# Q4972: Cooldown bypass on delete - threshold lowered in batch

## Question
Can an unprivileged attacker delete another member's pending request at the `added_timestamp + REQUEST_COOLDOWN` boundary so it can never accumulate confirmations, in a request that also carries `SetNumConfirmations { num_confirmations: 1 }`, breaking the invariant that only the intended parties can retire a pending request, and leading to protocol accounting value diverges from reality and another party settles on it?

## Target
- File/function: `multisig2/src/lib.rs` - `MultiSigContract::confirm / assert_valid_request / current_member`
- Entrypoint: `confirm(request_id)` - reachable by any predecessor the member set matches, and by the account itself through any of its access keys
- Attacker controls: the request id, the identity `current_member()` derives, and the timing relative to membership changes
- Exploit idea: Delete another member's pending request at the `added_timestamp + REQUEST_COOLDOWN` boundary so it can never accumulate confirmations, in a request that also carries `SetNumConfirmations { num_confirmations: 1 }`.
- Invariant to test: Only the intended parties can retire a pending request.
- Expected Immunefi impact: High - protocol accounting value diverges from reality and another party settles on it.
- Fast validation: Sim deletion at the boundary.
