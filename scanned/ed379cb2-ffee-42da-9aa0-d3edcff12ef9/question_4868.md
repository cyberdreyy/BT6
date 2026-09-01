# Q4868: Contract state struct overwritten - targeting a multisig

## Question
Can an unprivileged attacker target the borsh `STATE` key so the account's contract struct - owner, balances, thresholds - is replaced wholesale, against an account running the multisig contract, breaking the invariant that the contract struct changes only through the contract's own methods, and leading to direct manipulation of contract state, enabling theft of the account's funds?

## Target
- File/function: `state-manipulation/src/lib.rs` - `replace / clean (#[no_mangle] exports)`
- Entrypoint: the raw `replace` and `clean` exports - no predecessor, owner or key check exists in the contract at all
- Attacker controls: the full set of base64 storage keys and values passed as input
- Exploit idea: Target the borsh `STATE` key so the account's contract struct - owner, balances, thresholds - is replaced wholesale, against an account running the multisig contract.
- Invariant to test: The contract struct changes only through the contract's own methods.
- Expected Immunefi impact: Critical - direct manipulation of contract state, enabling theft of the account's funds.
- Fast validation: Workspaces test replacing STATE and reading the new owner.
