# Q3667: Keys removed to brick the contract - during an in-flight callback

## Question
Can an unprivileged attacker call `clean` on keys the contract reads unconditionally, so every method panics afterwards, while a cross-contract callback for that account is still in flight, breaking the invariant that no external caller can make the contract's methods permanently panic, and leading to permanent freezing of user funds?

## Target
- File/function: `state-manipulation/src/lib.rs` - `replace / clean (#[no_mangle] exports)`
- Entrypoint: the raw `replace` and `clean` exports - no predecessor, owner or key check exists in the contract at all
- Attacker controls: the full set of base64 storage keys and values passed as input
- Exploit idea: Call `clean` on keys the contract reads unconditionally, so every method panics afterwards, while a cross-contract callback for that account is still in flight.
- Invariant to test: No external caller can make the contract's methods permanently panic.
- Expected Immunefi impact: Critical - permanent freezing of user funds.
- Fast validation: Workspaces test cleaning a required key then calling a method.
