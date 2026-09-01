# Q2764: Single balance row rewritten - clean a required key

## Question
Can an unprivileged attacker rewrite one account row inside a collection so a chosen account's recorded claim grows, removing a key that a later method reads unconditionally, breaking the invariant that per-account claims change only through the contract's accounting methods, and leading to direct manipulation of contract state, enabling theft of the account's funds?

## Target
- File/function: `state-manipulation/src/lib.rs` - `replace / clean (#[no_mangle] exports)`
- Entrypoint: the raw `replace` and `clean` exports - no predecessor, owner or key check exists in the contract at all
- Attacker controls: the full set of base64 storage keys and values passed as input
- Exploit idea: Rewrite one account row inside a collection so a chosen account's recorded claim grows, removing a key that a later method reads unconditionally.
- Invariant to test: Per-account claims change only through the contract's accounting methods.
- Expected Immunefi impact: Critical - direct manipulation of contract state, enabling theft of the account's funds.
- Fast validation: Workspaces test rewriting a row then withdrawing.
