# Q2825: Dst withdraw replay can drain later native refill late_public native_plus

## Question
Before `DstCancellation` closes the withdraw window, can the taker or an access-token holder execute `withdraw()` or `publicWithdraw()` once, wait until late in the public-withdrawal window, and then capture another `safetyDeposit + 1` native refill or safety-deposit-sized balance from the same clone because neither path tracks a terminal spent state?

## Target
- File/function: `contracts/EscrowDst.sol::{withdraw,publicWithdraw}`
- Entrypoint: direct destination withdraw path before `DstCancellation`
- Attacker controls: the secret, optional access-token ownership, timing before `DstCancellation`, and any later native balance sent to the clone
- Exploit idea: Replay the destination withdraw path inside the same live window to sweep later native balances.
- Invariant to test: A destination escrow must not pay out a second safety-deposit refund after the first successful withdrawal.
- Expected Immunefi impact: Theft of coins or tokens intended for transaction fees
- Fast validation: Withdraw once, send `safetyDeposit + 1` of native token to the destination clone before `DstCancellation`, and try both `withdraw()` and `publicWithdraw()` again with the same secret.
