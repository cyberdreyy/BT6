# Q4004: Partial write leaves inconsistent structures - zero-length key

## Question
Can an unprivileged attacker write only some keys of a multi-key structure so the contract reads a half-updated view, with a zero-length key, breaking the invariant that structures are updated atomically, and leading to direct manipulation of contract state, enabling theft of the account's funds?

## Target
- File/function: `state-manipulation/src/lib.rs` - `replace / clean (#[no_mangle] exports)`
- Entrypoint: the raw `replace` and `clean` exports - no predecessor, owner or key check exists in the contract at all
- Attacker controls: the full set of base64 storage keys and values passed as input
- Exploit idea: Write only some keys of a multi-key structure so the contract reads a half-updated view, with a zero-length key.
- Invariant to test: Structures are updated atomically.
- Expected Immunefi impact: Critical - direct manipulation of contract state, enabling theft of the account's funds.
- Fast validation: Workspaces test a partial write then a read.
