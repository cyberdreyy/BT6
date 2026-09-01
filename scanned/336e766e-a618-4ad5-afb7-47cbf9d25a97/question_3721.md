# Q3721: Repeated transfers within one block - at the storage floor

## Question
Can an unprivileged attacker issue several `transfer` calls in one block so each is checked against the same pre-transfer `env::account_balance()`, when `env::account_balance()` is within `MIN_BALANCE_FOR_STORAGE` of zero so `get_account_balance` saturates to zero, breaking the invariant that the sum of transfers in one block is bounded by the liquid balance at its start, and leading to locked/unvested lockup tokens released to a party not entitled to them?

## Target
- File/function: `lockup/src/owner.rs` - `LockupContract::transfer / add_full_access_key`
- Entrypoint: `transfer(amount, receiver_id)` and `add_full_access_key(new_public_key)` - the lockup owner - a position anyone can hold, since `LockupFactory::create` is payable and open to any caller
- Attacker controls: the amount, the receiver, the timing, and every state the getters read
- Exploit idea: Issue several `transfer` calls in one block so each is checked against the same pre-transfer `env::account_balance()`, when `env::account_balance()` is within `MIN_BALANCE_FOR_STORAGE` of zero so `get_account_balance` saturates to zero.
- Invariant to test: The sum of transfers in one block is bounded by the liquid balance at its start.
- Expected Immunefi impact: Critical - locked/unvested lockup tokens released to a party not entitled to them.
- Fast validation: Sim a batch of transfers and sum them.
