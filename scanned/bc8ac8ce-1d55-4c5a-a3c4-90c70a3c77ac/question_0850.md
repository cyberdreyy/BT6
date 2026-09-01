# Q0850: Repeat calls racing a legitimate migration - a collection prefix

## Question
Can an unprivileged attacker call `replace` in the same block as the operator's own migration so the final state is the attacker's, targeting a collection prefix key such as the staking pool's accounts map prefix, breaking the invariant that only one principal can drive a migration, and leading to direct manipulation of contract state, enabling theft of the account's funds?

## Target
- File/function: `state-manipulation/src/lib.rs` - `replace / clean (#[no_mangle] exports)`
- Entrypoint: the raw `replace` and `clean` exports - no predecessor, owner or key check exists in the contract at all
- Attacker controls: the full set of base64 storage keys and values passed as input
- Exploit idea: Call `replace` in the same block as the operator's own migration so the final state is the attacker's, targeting a collection prefix key such as the staking pool's accounts map prefix.
- Invariant to test: Only one principal can drive a migration.
- Expected Immunefi impact: Critical - direct manipulation of contract state, enabling theft of the account's funds.
- Fast validation: Workspaces test concurrent calls.
