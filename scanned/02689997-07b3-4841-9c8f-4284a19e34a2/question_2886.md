# Q2886: Dst withdraw replay can drain later native refill near_rescue token_dust

## Question
Before `DstCancellation` closes the withdraw window, can the taker or an access-token holder execute `withdraw()` or `publicWithdraw()` once, wait until just before `RESCUE_DELAY`, and then capture another `amount / 100` native refill or safety-deposit-sized balance from the same clone because neither path tracks a terminal spent state?

## Target
- File/function: `contracts/EscrowDst.sol::{withdraw,publicWithdraw}`
- Entrypoint: direct destination withdraw path before `DstCancellation`
- Attacker controls: the secret, optional access-token ownership, timing before `DstCancellation`, and any later native balance sent to the clone
- Exploit idea: Replay the destination withdraw path inside the same live window to sweep later native balances.
- Invariant to test: A destination escrow must not pay out a second safety-deposit refund after the first successful withdrawal.
- Expected Immunefi impact: Theft of coins or tokens intended for transaction fees
- Fast validation: Withdraw once, send `amount / 100` of native token to the destination clone before `DstCancellation`, and try both `withdraw()` and `publicWithdraw()` again with the same secret.
