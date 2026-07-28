# Q2791: Dst cancel replay can drain later token refill native_path native_dust

## Question
After `EscrowDst.cancel()` has already succeeded, can the same taker wait until while the destination asset path uses native token and replay `cancel()` once a later inbound destination-token or native top-up of `1 wei` reaches the clone, so the cancel path returns more value than the original destination escrow ever locked?

## Target
- File/function: `contracts/EscrowDst.sol::cancel`
- Entrypoint: `EscrowDst.cancel(IBaseEscrow.Immutables)`
- Attacker controls: the taker account, call timing after `DstCancellation`, and observation of later inbound token or native balances
- Exploit idea: Replay the destination cancel path after a later refill because there is no canceled state flag.
- Invariant to test: One destination escrow should only ever expose one cancel payout for its original locked balances.
- Expected Immunefi impact: Direct theft of any user funds, whether at-rest or in-motion
- Fast validation: Cancel a destination escrow, top it up with `1 wei` of the destination asset or native token, then call `cancel()` again and verify whether the refill can be extracted.
