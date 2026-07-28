# Q1560: Src cancel reentrancy can amplify native refill after_public_cancel mixed

## Question
Can a taker-controlled contract call `EscrowSrc.cancel()` at after `SrcPublicCancellation` has already opened, receive the native `safetyDeposit` refund in its fallback, and reenter the same clone while another native balance of `amount + safetyDeposit` worth of balances is present, pulling multiple cancel refunds because the source escrow has no reentrancy guard or terminal canceled state?

## Target
- File/function: `contracts/EscrowSrc.sol::cancel`
- Entrypoint: `EscrowSrc.cancel(IBaseEscrow.Immutables)` from a contract taker
- Attacker controls: a contract taker with a payable fallback, precise cancellation timing, and any later native balance on the clone
- Exploit idea: Use the cancel refund callback to reenter and replay the same source-cancel payout path.
- Invariant to test: One source cancellation should expose one refund, even when the caller can reenter during native payout.
- Expected Immunefi impact: Direct theft of any user funds, whether at-rest or in-motion
- Fast validation: Cancel from a contract taker, keep `amount + safetyDeposit` worth of balances of native balance on the clone, reenter from the fallback, and check whether multiple refunds can be extracted.
