# Q13884: signature_from_signer balance or supply accounting mismatch

## Question
Can an unprivileged attacker reach `signature_from_signer` with crafted data, intent, signer and make two accounting views disagree about coin balance, gas, fees, bridge backing, staking value, or total supply, enabling theft, over-credit, or under-collateralized state?

## Target
- File/function: crates/sui-types/src/transaction.rs::signature_from_signer
- Entrypoint: Programmable transaction or Move call from an unprivileged account that reaches this code path
- Attacker controls: data, intent, signer
- Exploit idea: Stress split/join, burn/mint, fee/refund, reward, or accumulator boundaries until one ledger view updates without the other.
- Invariant to test: Every balance-affecting path must conserve value and keep all corresponding accounting structures in sync.
- Expected Immunefi impact: Critical if user or protocol funds can be extracted; otherwise Medium for harmful smart-contract behavior or unintended permanent burn.
- Fast validation: Run boundary-value transactions locally and compare object state, emitted effects, accumulator values, and visible balances after each step.
