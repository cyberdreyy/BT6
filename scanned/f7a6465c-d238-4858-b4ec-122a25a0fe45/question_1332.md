# Q1332: Refund receipt credited as a deposit - amount = balance - 1

## Question
Can an unprivileged attacker arrange for a refund receipt to land on the pool account and be swept into the accounting as someone's balance or as reward, with `amount` one yoctoNEAR below the attacker's own recorded balance, breaking the invariant that only explicit `deposit` calls create account credit, and leading to protocol accounting value diverges from reality and another party settles on it?

## Target
- File/function: `staking-pool/src/internal.rs` - `StakingContract::internal_deposit / deposit / deposit_and_stake`
- Entrypoint: `deposit()` and `deposit_and_stake()` - payable, any account
- Attacker controls: the attached deposit, the account id it credits, and the surrounding epoch state
- Exploit idea: Arrange for a refund receipt to land on the pool account and be swept into the accounting as someone's balance or as reward, with `amount` one yoctoNEAR below the attacker's own recorded balance.
- Invariant to test: Only explicit `deposit` calls create account credit.
- Expected Immunefi impact: High - protocol accounting value diverges from reality and another party settles on it.
- Fast validation: Sim a refund into the pool and check the accounting.
