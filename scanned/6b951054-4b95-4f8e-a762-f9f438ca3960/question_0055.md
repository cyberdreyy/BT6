# Q55: pick_highest_balance_coin balance or supply accounting mismatch

## Question
Can an unprivileged attacker reach `pick_highest_balance_coin` with crafted client, address, minimal_amount and make two accounting views disagree about coin balance, gas, fees, bridge backing, staking value, or total supply, enabling theft, over-credit, or under-collateralized state?

## Target
- File/function: crates/sui-bridge/src/config.rs::pick_highest_balance_coin
- Entrypoint: Programmable transaction or Move call from an unprivileged account that reaches this code path
- Attacker controls: client, address, minimal_amount
- Exploit idea: Stress split/join, burn/mint, fee/refund, reward, or accumulator boundaries until one ledger view updates without the other.
- Invariant to test: Every balance-affecting path must conserve value and keep all corresponding accounting structures in sync.
- Expected Immunefi impact: Critical if user or protocol funds can be extracted; otherwise Medium for harmful smart-contract behavior or unintended permanent burn.
- Fast validation: Run boundary-value transactions locally and compare object state, emitted effects, accumulator values, and visible balances after each step.
