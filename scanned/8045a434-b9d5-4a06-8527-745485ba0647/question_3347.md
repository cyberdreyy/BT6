# Q3347: Deposit inside the reward window - right after pool creation

## Question
Can an unprivileged attacker deposit in the exact receipt where the epoch reward is being folded in, so the deposit is momentarily indistinguishable from reward, on a pool created moments earlier through the public `create_staking_pool`, breaking the invariant that deposited NEAR is never distributed as reward to other accounts, and leading to theft of staking rewards or fees owed to other parties?

## Target
- File/function: `staking-pool/src/internal.rs` - `StakingContract::internal_deposit / deposit / deposit_and_stake`
- Entrypoint: `deposit()` and `deposit_and_stake()` - payable, any account
- Attacker controls: the attached deposit, the account id it credits, and the surrounding epoch state
- Exploit idea: Deposit in the exact receipt where the epoch reward is being folded in, so the deposit is momentarily indistinguishable from reward, on a pool created moments earlier through the public `create_staking_pool`.
- Invariant to test: Deposited NEAR is never distributed as reward to other accounts.
- Expected Immunefi impact: High - theft of staking rewards or fees owed to other parties.
- Fast validation: Sim deposit at the ping boundary and compare delegator claims.
