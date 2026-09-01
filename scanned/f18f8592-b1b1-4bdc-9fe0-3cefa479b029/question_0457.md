# Q0457: Refund receipt credited as a deposit - chained in one receipt

## Question
Can an unprivileged attacker arrange for a refund receipt to land on the pool account and be swept into the accounting as someone's balance or as reward, in a single transaction where the preceding action already moved `total_staked_balance`, breaking the invariant that only explicit `deposit` calls create account credit, and leading to protocol accounting value diverges from reality and another party settles on it?

## Target
- File/function: `staking-pool/src/internal.rs` - `StakingContract::internal_deposit / deposit / deposit_and_stake`
- Entrypoint: `deposit()` and `deposit_and_stake()` - payable, any account
- Attacker controls: the attached deposit, the account id it credits, and the surrounding epoch state
- Exploit idea: Arrange for a refund receipt to land on the pool account and be swept into the accounting as someone's balance or as reward, in a single transaction where the preceding action already moved `total_staked_balance`.
- Invariant to test: Only explicit `deposit` calls create account credit.
- Expected Immunefi impact: High - protocol accounting value diverges from reality and another party settles on it.
- Fast validation: Sim a refund into the pool and check the accounting.
