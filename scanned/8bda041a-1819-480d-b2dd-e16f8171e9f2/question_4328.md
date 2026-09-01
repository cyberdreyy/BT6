# Q4328: Transfers flipped on in the same receipt as a transfer - balance seeded before init

## Question
Can an unprivileged attacker chain the poll callback and a `transfer` so the release gate is evaluated against a state that flips mid-transaction, on a lockup whose account already held NEAR when `new` ran, so `lockup_amount` came from a balance an outsider influenced, breaking the invariant that the release gate is evaluated against settled state, and leading to locked/unvested lockup tokens released to a party not entitled to them?

## Target
- File/function: `lockup/src/owner_callbacks.rs` - `on_whitelist_is_whitelisted / on_staking_pool_* / on_get_account_total_balance / on_get_result_from_transfer_poll`
- Entrypoint: callbacks reached through the owner methods; their inputs come from contracts the caller named
- Attacker controls: the contract that supplies the callback value and the success or failure of the promise
- Exploit idea: Chain the poll callback and a `transfer` so the release gate is evaluated against a state that flips mid-transaction, on a lockup whose account already held NEAR when `new` ran, so `lockup_amount` came from a balance an outsider influenced.
- Invariant to test: The release gate is evaluated against settled state.
- Expected Immunefi impact: Critical - locked/unvested lockup tokens released to a party not entitled to them.
- Fast validation: Sim the chain and reconcile.
