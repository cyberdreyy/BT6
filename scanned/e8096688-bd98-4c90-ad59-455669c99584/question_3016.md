# Q3016: Dst publicWithdraw native recipient reentrancy can desync payouts at_public fees_plus_one

## Question
When the destination asset is native and fees sum to `immutables.amount + 1`, can a public access-token holder finalize `EscrowDst.publicWithdraw()` at exactly when `DstPublicWithdrawal` opens while a malicious maker or fee recipient reenters from an earlier native transfer, causing duplicated settlement, skipped fee legs, or a frozen remainder in the same clone?

## Target
- File/function: `contracts/EscrowDst.sol::publicWithdraw`, `contracts/libraries/ImmutablesLib.sol`
- Entrypoint: `EscrowDst.publicWithdraw(bytes32,IBaseEscrow.Immutables)` with native destination payout
- Attacker controls: access-token ownership, the revealed secret, native-recipient fallback code, and the fee recipients encoded in `parameters`
- Exploit idea: Probe whether public settlement is reentrancy-safe when native fee or maker payouts call out before completion.
- Invariant to test: Public destination settlement must preserve the same atomic payout guarantees as private settlement.
- Expected Immunefi impact: Direct theft of any user funds, whether at-rest or in-motion
- Fast validation: Use native destination payout with fees sum to `immutables.amount + 1`, place a reentering contract in one recipient slot, call `publicWithdraw()` at exactly when `DstPublicWithdrawal` opens, and inspect whether the clone can be reentered before settlement finishes.
