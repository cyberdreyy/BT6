# Q0982: Refund receipt credited as a deposit - last block of an epoch

## Question
Can an unprivileged attacker arrange for a refund receipt to land on the pool account and be swept into the accounting as someone's balance or as reward, in the last block of an epoch so the state change lands before the reward is distributed, breaking the invariant that only explicit `deposit` calls create account credit, and leading to protocol accounting value diverges from reality and another party settles on it?

## Target
- File/function: `staking-pool/src/internal.rs` - `StakingContract::internal_deposit / deposit / deposit_and_stake`
- Entrypoint: `deposit()` and `deposit_and_stake()` - payable, any account
- Attacker controls: the attached deposit, the account id it credits, and the surrounding epoch state
- Exploit idea: Arrange for a refund receipt to land on the pool account and be swept into the accounting as someone's balance or as reward, in the last block of an epoch so the state change lands before the reward is distributed.
- Invariant to test: Only explicit `deposit` calls create account credit.
- Expected Immunefi impact: High - protocol accounting value diverges from reality and another party settles on it.
- Fast validation: Sim a refund into the pool and check the accounting.
