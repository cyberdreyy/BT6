# Q0932: Deposit inside the reward window - last block of an epoch

## Question
Can an unprivileged attacker deposit in the exact receipt where the epoch reward is being folded in, so the deposit is momentarily indistinguishable from reward, in the last block of an epoch so the state change lands before the reward is distributed, breaking the invariant that deposited NEAR is never distributed as reward to other accounts, and leading to theft of staking rewards or fees owed to other parties?

## Target
- File/function: `staking-pool/src/internal.rs` - `StakingContract::internal_deposit / deposit / deposit_and_stake`
- Entrypoint: `deposit()` and `deposit_and_stake()` - payable, any account
- Attacker controls: the attached deposit, the account id it credits, and the surrounding epoch state
- Exploit idea: Deposit in the exact receipt where the epoch reward is being folded in, so the deposit is momentarily indistinguishable from reward, in the last block of an epoch so the state change lands before the reward is distributed.
- Invariant to test: Deposited NEAR is never distributed as reward to other accounts.
- Expected Immunefi impact: High - theft of staking rewards or fees owed to other parties.
- Fast validation: Sim deposit at the ping boundary and compare delegator claims.
