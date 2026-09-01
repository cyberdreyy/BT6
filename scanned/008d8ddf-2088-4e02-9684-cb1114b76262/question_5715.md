# Q5715: Owners_balance inflated by deposit_amount - just after unselect

## Question
Can an unprivileged attacker inflate `get_known_deposited_balance()` so `get_owners_balance` reports account NEAR that is actually locked as available, in the receipt right after `unselect_staking_pool` cleared the staking information, breaking the invariant that `get_owners_balance()` never exceeds real assets minus the locked amount, and leading to locked/unvested lockup tokens released to a party not entitled to them?

## Target
- File/function: `lockup/src/getters.rs` - `get_locked_amount / get_unvested_amount / get_owners_balance / get_liquid_owners_balance`
- Entrypoint: read by `transfer`, `add_full_access_key` and every staking method as the release gate
- Attacker controls: the schedule arguments chosen at creation, the account balance, and the block timestamp it is called at
- Exploit idea: Inflate `get_known_deposited_balance()` so `get_owners_balance` reports account NEAR that is actually locked as available, in the receipt right after `unselect_staking_pool` cleared the staking information.
- Invariant to test: `get_owners_balance()` never exceeds real assets minus the locked amount.
- Expected Immunefi impact: Critical - locked/unvested lockup tokens released to a party not entitled to them.
- Fast validation: Sim an inflated deposit_amount and attempt a transfer.
