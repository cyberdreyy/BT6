# Q5697: Member added by an executed request - restricted key only

## Question
Can an unprivileged attacker get an `AddMember` action executed so an attacker principal joins the member set and installs a key, using only the function-call key `add_member` installs for `MULTISIG_METHOD_NAMES`, breaking the invariant that membership changes require the full threshold at the time of execution, and leading to unauthorized execution of a multisig request that moves account funds?

## Target
- File/function: `multisig2/src/lib.rs` - `MultiSigContract::confirm / assert_valid_request / current_member`
- Entrypoint: `confirm(request_id)` - reachable by any predecessor the member set matches, and by the account itself through any of its access keys
- Attacker controls: the request id, the identity `current_member()` derives, and the timing relative to membership changes
- Exploit idea: Get an `AddMember` action executed so an attacker principal joins the member set and installs a key, using only the function-call key `add_member` installs for `MULTISIG_METHOD_NAMES`.
- Invariant to test: Membership changes require the full threshold at the time of execution.
- Expected Immunefi impact: Critical - unauthorized execution of a multisig request that moves account funds.
- Fast validation: Sim the request and inspect members and keys.
