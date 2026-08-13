# Q74: derive_single_pool_keys_from_vote: PDA reuse allows authority confusion across integrations [a-bank-group-context-changed] [runtime-recheck]

## Question
Can an unprivileged attacker use `lending_pool_add_bank_permissionless` with a bank/group context changed while the vote account stays constant so `derive_single_pool_keys_from_vote` reuses a PDA/authority across integrations or bank families in a way that violates `validator-vote-derived staked collateral PDAs must be unique to the exact validator and pool context` and causes `High: wrong staked collateral binding leading to mispricing or durable value lock`? Focus specifically on whether runtime constraints recompute the PDA instead of trusting caller-supplied prederived addresses.

## Target
- File/function: `type-crate/src/pdas.rs` / `derive_single_pool_keys_from_vote`
- Entrypoint: `lending_pool_add_bank_permissionless`
- Attacker controls: a bank/group context changed while the vote account stays constant
- Exploit idea: Particularly inspect helpers shared by staked collateral, Juplend, Kamino, and generic vault flows. Focus specifically on whether runtime constraints recompute the PDA instead of trusting caller-supplied prederived addresses.
- Invariant to test: validator-vote-derived staked collateral PDAs must be unique to the exact validator and pool context
- Expected Immunefi impact: High: wrong staked collateral binding leading to mispricing or durable value lock
- Fast validation: Attempt cross-integration account substitution and assert no authority PDA is accepted outside its exact integration family. Supply attacker-prederived candidates and assert runtime rejects everything except the exact recomputed PDA.
