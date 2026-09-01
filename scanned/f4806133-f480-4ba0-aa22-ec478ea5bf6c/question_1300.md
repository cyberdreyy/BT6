# Q1300: Collection length desynchronised - length bookkeeping

## Question
Can an unprivileged attacker write or clean the length and index keys of a persistent collection so iteration and lookup disagree, targeting the length/index bookkeeping key of an `UnorderedMap`, breaking the invariant that collection metadata always matches its contents, and leading to direct manipulation of contract state, enabling theft of the account's funds?

## Target
- File/function: `state-manipulation/src/lib.rs` - `replace / clean (#[no_mangle] exports)`
- Entrypoint: the raw `replace` and `clean` exports - no predecessor, owner or key check exists in the contract at all
- Attacker controls: the full set of base64 storage keys and values passed as input
- Exploit idea: Write or clean the length and index keys of a persistent collection so iteration and lookup disagree, targeting the length/index bookkeeping key of an `UnorderedMap`.
- Invariant to test: Collection metadata always matches its contents.
- Expected Immunefi impact: Critical - direct manipulation of contract state, enabling theft of the account's funds.
- Fast validation: Workspaces test the collection after manipulation.
