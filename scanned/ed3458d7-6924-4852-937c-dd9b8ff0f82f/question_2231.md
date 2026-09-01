# Q2231: Default row treated as a real account - first delegator

## Question
Can an unprivileged attacker act from an account id that has never been stored so `Account::default()` supplies zeros the caller can exploit, as the first delegator, while `total_stake_shares` is still only the seeded `STAKE_SHARE_PRICE_GUARANTEE_FUND`, breaking the invariant that an account with no stored row cannot be credited without a deposit, and leading to direct theft of delegator NEAR from the staking pool?

## Target
- File/function: `staking-pool/src/internal.rs` - `internal_get_account / internal_save_account / get_account`
- Entrypoint: any delegator method, plus the public view methods `get_account`, `get_accounts`, `get_number_of_accounts`
- Attacker controls: when its row exists, when it is deleted, and what an integrator reads from the views
- Exploit idea: Act from an account id that has never been stored so `Account::default()` supplies zeros the caller can exploit, as the first delegator, while `total_stake_shares` is still only the seeded `STAKE_SHARE_PRICE_GUARANTEE_FUND`.
- Invariant to test: An account with no stored row cannot be credited without a deposit.
- Expected Immunefi impact: Critical - direct theft of delegator NEAR from the staking pool.
- Fast validation: Unit test each method from an unknown account.
