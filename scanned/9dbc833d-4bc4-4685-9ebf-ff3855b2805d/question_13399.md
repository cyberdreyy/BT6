# Q13399: is_argument_used balance or supply accounting mismatch

## Question
Can an unprivileged attacker reach `is_argument_used` with crafted argument and make two accounting views disagree about coin balance, gas, fees, bridge backing, staking value, or total supply, enabling theft, over-credit, or under-collateralized state?

## Target
- File/function: crates/sui-types/src/transaction.rs::is_argument_used
- Entrypoint: Programmable transaction or Move call from an unprivileged account that reaches this code path
- Attacker controls: argument
- Exploit idea: Stress split/join, burn/mint, fee/refund, reward, or accumulator boundaries until one ledger view updates without the other.
- Invariant to test: Every balance-affecting path must conserve value and keep all corresponding accounting structures in sync.
- Expected Immunefi impact: Critical if user or protocol funds can be extracted; otherwise Medium for harmful smart-contract behavior or unintended permanent burn.
- Fast validation: Run boundary-value transactions locally and compare object state, emitted effects, accumulator values, and visible balances after each step.
