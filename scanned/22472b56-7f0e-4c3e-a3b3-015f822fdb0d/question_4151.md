# Q4151: Single balance row rewritten - ordinary predecessor

## Question
Can an unprivileged attacker rewrite one account row inside a collection so a chosen account's recorded claim grows, from an ordinary account that is neither the contract account nor its deployer, breaking the invariant that per-account claims change only through the contract's accounting methods, and leading to direct manipulation of contract state, enabling theft of the account's funds?

## Target
- File/function: `state-manipulation/src/lib.rs` - `replace / clean (#[no_mangle] exports)`
- Entrypoint: the raw `replace` and `clean` exports - no predecessor, owner or key check exists in the contract at all
- Attacker controls: the full set of base64 storage keys and values passed as input
- Exploit idea: Rewrite one account row inside a collection so a chosen account's recorded claim grows, from an ordinary account that is neither the contract account nor its deployer.
- Invariant to test: Per-account claims change only through the contract's accounting methods.
- Expected Immunefi impact: Critical - direct manipulation of contract state, enabling theft of the account's funds.
- Fast validation: Workspaces test rewriting a row then withdrawing.
