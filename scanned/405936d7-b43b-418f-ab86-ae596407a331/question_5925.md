# Q5925: Add_request_and_confirm as a one-shot execution - num_confirmations = 0

## Question
Can an unprivileged attacker use `add_request_and_confirm` on a contract whose threshold is one, or was made one, to move funds in a single call, on a multisig deployed through `MultisigFactory::create` with `num_confirmations = 0`, breaking the invariant that a single call can never both create and execute a value-moving request unless the threshold genuinely is one, and leading to unauthorized execution of a multisig request that moves account funds?

## Target
- File/function: `multisig2/src/lib.rs` - `MultiSigContract::confirm / assert_valid_request / current_member`
- Entrypoint: `confirm(request_id)` - reachable by any predecessor the member set matches, and by the account itself through any of its access keys
- Attacker controls: the request id, the identity `current_member()` derives, and the timing relative to membership changes
- Exploit idea: Use `add_request_and_confirm` on a contract whose threshold is one, or was made one, to move funds in a single call, on a multisig deployed through `MultisigFactory::create` with `num_confirmations = 0`.
- Invariant to test: A single call can never both create and execute a value-moving request unless the threshold genuinely is one.
- Expected Immunefi impact: Critical - unauthorized execution of a multisig request that moves account funds.
- Fast validation: Sim the call at threshold one.
