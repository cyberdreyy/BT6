# Q4084: Cooldown bypass on delete - receiver_id = self

## Question
Can an unprivileged attacker delete another member's pending request at the `added_timestamp + REQUEST_COOLDOWN` boundary so it can never accumulate confirmations, with `receiver_id` set to `env::current_account_id()` so `assert_self_request` passes, breaking the invariant that only the intended parties can retire a pending request, and leading to protocol accounting value diverges from reality and another party settles on it?

## Target
- File/function: `multisig2/src/lib.rs` - `MultiSigContract::confirm / assert_valid_request / current_member`
- Entrypoint: `confirm(request_id)` - reachable by any predecessor the member set matches, and by the account itself through any of its access keys
- Attacker controls: the request id, the identity `current_member()` derives, and the timing relative to membership changes
- Exploit idea: Delete another member's pending request at the `added_timestamp + REQUEST_COOLDOWN` boundary so it can never accumulate confirmations, with `receiver_id` set to `env::current_account_id()` so `assert_self_request` passes.
- Invariant to test: Only the intended parties can retire a pending request.
- Expected Immunefi impact: High - protocol accounting value diverges from reality and another party settles on it.
- Fast validation: Sim deletion at the boundary.
