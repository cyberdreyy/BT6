# Q1126: Src withdraw replay after native top-up at_public_open almost_full

## Question
After one successful `EscrowSrc.withdraw`, can the same taker wait until exactly when `SrcPublicWithdrawal` starts and replay `withdraw(secret, immutables)` after a later native-token top-up of `amount - 1` reaches the clone, collecting another fixed `safetyDeposit` refund even if no new maker-token payout is due?

## Target
- File/function: `contracts/EscrowSrc.sol::withdraw`
- Entrypoint: `EscrowSrc.withdraw(bytes32,IBaseEscrow.Immutables)`
- Attacker controls: the secret, replay timing before `SrcCancellation`, and observation of later native-token balances sent to the clone
- Exploit idea: Replay the private withdraw path to harvest a second safety-deposit refund from later native balances.
- Invariant to test: A source escrow should only pay one safety-deposit refund for one successful withdrawal lifecycle.
- Expected Immunefi impact: Theft of coins or tokens intended for transaction fees
- Fast validation: Withdraw once, send `amount - 1` of native token to the source clone before `SrcCancellation`, call `withdraw()` again with the same secret, and inspect whether a second `safetyDeposit` is paid.
