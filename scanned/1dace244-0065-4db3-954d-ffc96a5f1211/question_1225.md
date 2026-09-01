# Q1225: Arbitrary storage write with no caller check - length bookkeeping

## Question
Can an unprivileged attacker call the exported `replace` from any account, since it reads `input()` and calls `storage_write` for every caller-supplied key/value pair with no authorisation of any kind, targeting the length/index bookkeeping key of an `UnorderedMap`, breaking the invariant that only an authorised principal can write this account's storage, and leading to direct manipulation of contract state, enabling theft of the account's funds?

## Target
- File/function: `state-manipulation/src/lib.rs` - `replace / clean (#[no_mangle] exports)`
- Entrypoint: the raw `replace` and `clean` exports - no predecessor, owner or key check exists in the contract at all
- Attacker controls: the full set of base64 storage keys and values passed as input
- Exploit idea: Call the exported `replace` from any account, since it reads `input()` and calls `storage_write` for every caller-supplied key/value pair with no authorisation of any kind, targeting the length/index bookkeeping key of an `UnorderedMap`.
- Invariant to test: Only an authorised principal can write this account's storage.
- Expected Immunefi impact: Critical - direct manipulation of contract state, enabling theft of the account's funds.
- Fast validation: `near-workspaces` test: deploy, call `replace` from an unrelated account, and read the changed state.
