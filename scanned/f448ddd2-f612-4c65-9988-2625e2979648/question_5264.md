# Q5264: Evicted register data leaked into the next write - targeting wNEAR

## Question
Can an unprivileged attacker use the `EVICTED_REGISTER` reuse across successive `storage_write` and `storage_remove` calls to carry data between entries, against the account running the wNEAR contract, breaking the invariant that each write uses only its own supplied value, and leading to direct manipulation of contract state, enabling theft of the account's funds?

## Target
- File/function: `state-manipulation/src/lib.rs` - `replace / clean (#[no_mangle] exports)`
- Entrypoint: the raw `replace` and `clean` exports - no predecessor, owner or key check exists in the contract at all
- Attacker controls: the full set of base64 storage keys and values passed as input
- Exploit idea: Use the `EVICTED_REGISTER` reuse across successive `storage_write` and `storage_remove` calls to carry data between entries, against the account running the wNEAR contract.
- Invariant to test: Each write uses only its own supplied value.
- Expected Immunefi impact: Critical - direct manipulation of contract state, enabling theft of the account's funds.
- Fast validation: Workspaces test successive writes and read back each key.
