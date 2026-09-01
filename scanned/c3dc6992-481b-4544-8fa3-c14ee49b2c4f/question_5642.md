# Q5642: Active request limit as a lockout - restricted key only

## Question
Can an unprivileged attacker fill another member's `num_requests_pk` so they can never add the request needed to fix the contract, using only the function-call key `add_member` installs for `MULTISIG_METHOD_NAMES`, breaking the invariant that one member cannot exhaust another member's request budget, and leading to permanent freezing of user funds?

## Target
- File/function: `multisig2/src/lib.rs` - `MultiSigContract::confirm / assert_valid_request / current_member`
- Entrypoint: `confirm(request_id)` - reachable by any predecessor the member set matches, and by the account itself through any of its access keys
- Attacker controls: the request id, the identity `current_member()` derives, and the timing relative to membership changes
- Exploit idea: Fill another member's `num_requests_pk` so they can never add the request needed to fix the contract, using only the function-call key `add_member` installs for `MULTISIG_METHOD_NAMES`.
- Invariant to test: One member cannot exhaust another member's request budget.
- Expected Immunefi impact: Critical - permanent freezing of user funds.
- Fast validation: Sim the fill and attempt an honest request.
