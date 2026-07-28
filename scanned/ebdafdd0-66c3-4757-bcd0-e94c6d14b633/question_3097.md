# Q3097: Dst publicWithdraw native recipient reentrancy can desync payouts native_path fees_overflowish

## Question
When the destination asset is native and one fee is tiny and the other is near the full amount, can a public access-token holder finalize `EscrowDst.publicWithdraw()` at while the destination asset path uses native token while a malicious maker or fee recipient reenters from an earlier native transfer, causing duplicated settlement, skipped fee legs, or a frozen remainder in the same clone?

## Target
- File/function: `contracts/EscrowDst.sol::publicWithdraw`, `contracts/libraries/ImmutablesLib.sol`
- Entrypoint: `EscrowDst.publicWithdraw(bytes32,IBaseEscrow.Immutables)` with native destination payout
- Attacker controls: access-token ownership, the revealed secret, native-recipient fallback code, and the fee recipients encoded in `parameters`
- Exploit idea: Probe whether public settlement is reentrancy-safe when native fee or maker payouts call out before completion.
- Invariant to test: Public destination settlement must preserve the same atomic payout guarantees as private settlement.
- Expected Immunefi impact: Direct theft of any user funds, whether at-rest or in-motion
- Fast validation: Use native destination payout with one fee is tiny and the other is near the full amount, place a reentering contract in one recipient slot, call `publicWithdraw()` at while the destination asset path uses native token, and inspect whether the clone can be reentered before settlement finishes.
