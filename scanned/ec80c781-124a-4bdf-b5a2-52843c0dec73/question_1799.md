# Q1799: Registration of an unusable account id - receiver over-refunds

## Question
Can an unprivileged attacker register an account id that can never call back, permanently retaining its storage NEAR and any balance, with a `receiver_id` contract that returns an unused amount larger than the amount transferred, breaking the invariant that every registered account can act on its balance, and leading to permanent freezing of user funds?

## Target
- File/function: `w-near/src/legacy_storage.rs` - `impl_fungible_token_storage! - storage_deposit / storage_withdraw / storage_unregister / storage_minimum_balance`
- Entrypoint: the storage-management methods on wNEAR - any account
- Attacker controls: the deposit amounts, the `force` flag and the registration lifecycle
- Exploit idea: Register an account id that can never call back, permanently retaining its storage NEAR and any balance, with a `receiver_id` contract that returns an unused amount larger than the amount transferred.
- Invariant to test: Every registered account can act on its balance.
- Expected Immunefi impact: Critical - permanent freezing of user funds.
- Fast validation: Sim registration of such an id.
