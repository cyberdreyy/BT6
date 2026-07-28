# Q1455: Src cancel replay after native top-up after_public_cancel half

## Question
After `EscrowSrc.cancel()` has already succeeded, can the same taker wait until after `SrcPublicCancellation` has already opened, send or observe a later native-token top-up of `amount / 2` to the clone, and call `cancel()` again to pull another fixed `safetyDeposit` refund because `_cancel()` never records that the escrow was already canceled?

## Target
- File/function: `contracts/EscrowSrc.sol::cancel`
- Entrypoint: `EscrowSrc.cancel(IBaseEscrow.Immutables)`
- Attacker controls: call timing after `SrcCancellation`, the taker account, and any later native-token balance added to the clone
- Exploit idea: Replay the private cancel path to drain later native balances via the fixed safety-deposit refund.
- Invariant to test: Canceling one source escrow once must fully consume the safety-deposit refund right for that clone.
- Expected Immunefi impact: Direct theft of any user funds, whether at-rest or in-motion
- Fast validation: Cancel a live source escrow, send `amount / 2` of native token to it, call `cancel()` again, and check whether another `safetyDeposit` payment is released.
