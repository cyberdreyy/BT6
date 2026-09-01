# Q1975: Panic path on malformed base64 - empty value

## Question
Can an unprivileged attacker supply keys or values that fail `base64::decode` so the call unwraps and aborts midway through a batch, writing an empty value over a populated key, breaking the invariant that a batch of writes is atomic, and leading to direct manipulation of contract state, enabling theft of the account's funds?

## Target
- File/function: `state-manipulation/src/lib.rs` - `replace / clean (#[no_mangle] exports)`
- Entrypoint: the raw `replace` and `clean` exports - no predecessor, owner or key check exists in the contract at all
- Attacker controls: the full set of base64 storage keys and values passed as input
- Exploit idea: Supply keys or values that fail `base64::decode` so the call unwraps and aborts midway through a batch, writing an empty value over a populated key.
- Invariant to test: A batch of writes is atomic.
- Expected Immunefi impact: Critical - direct manipulation of contract state, enabling theft of the account's funds.
- Fast validation: Workspaces test a batch with one invalid entry.
