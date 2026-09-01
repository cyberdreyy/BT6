# Q1982: Deposit inside the reward window - first delegator

## Question
Can an unprivileged attacker deposit in the exact receipt where the epoch reward is being folded in, so the deposit is momentarily indistinguishable from reward, as the first delegator, while `total_stake_shares` is still only the seeded `STAKE_SHARE_PRICE_GUARANTEE_FUND`, breaking the invariant that deposited NEAR is never distributed as reward to other accounts, and leading to theft of staking rewards or fees owed to other parties?

## Target
- File/function: `staking-pool/src/internal.rs` - `StakingContract::internal_deposit / deposit / deposit_and_stake`
- Entrypoint: `deposit()` and `deposit_and_stake()` - payable, any account
- Attacker controls: the attached deposit, the account id it credits, and the surrounding epoch state
- Exploit idea: Deposit in the exact receipt where the epoch reward is being folded in, so the deposit is momentarily indistinguishable from reward, as the first delegator, while `total_stake_shares` is still only the seeded `STAKE_SHARE_PRICE_GUARANTEE_FUND`.
- Invariant to test: Deposited NEAR is never distributed as reward to other accounts.
- Expected Immunefi impact: High - theft of staking rewards or fees owed to other parties.
- Fast validation: Sim deposit at the ping boundary and compare delegator claims.
