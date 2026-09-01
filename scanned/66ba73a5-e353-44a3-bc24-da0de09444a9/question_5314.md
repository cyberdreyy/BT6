# Q5314: Member set drops below the threshold - dual member identity

## Question
Can an unprivileged attacker drive the member count under `num_confirmations` so no request can ever be confirmed again and the account's funds are stuck, when one principal is registered both as `MultisigMember::Account` and as `MultisigMember::AccessKey`, breaking the invariant that the member set always supports the configured threshold, and leading to permanent freezing of user funds?

## Target
- File/function: `multisig2/src/lib.rs` - `MultiSigContract::confirm / assert_valid_request / current_member`
- Entrypoint: `confirm(request_id)` - reachable by any predecessor the member set matches, and by the account itself through any of its access keys
- Attacker controls: the request id, the identity `current_member()` derives, and the timing relative to membership changes
- Exploit idea: Drive the member count under `num_confirmations` so no request can ever be confirmed again and the account's funds are stuck, when one principal is registered both as `MultisigMember::Account` and as `MultisigMember::AccessKey`.
- Invariant to test: The member set always supports the configured threshold.
- Expected Immunefi impact: Critical - permanent freezing of user funds.
- Fast validation: Sim removals to the boundary and attempt a request.
