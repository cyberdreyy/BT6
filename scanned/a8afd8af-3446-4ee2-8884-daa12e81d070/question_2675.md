# Q2675: Dst publicWithdraw fee boundary can mispay fees_protocol_full same_block

## Question
At in the same block as the first destination action, can a public access-token holder finalize a destination escrow whose `parameters` are crafted so that the protocol fee alone is near the full amount, causing `publicWithdraw()` to overpay fee recipients, underflow the maker payout, or leave the clone insolvent while still paying out the safety deposit?

## Target
- File/function: `contracts/EscrowDst.sol::publicWithdraw`, `contracts/libraries/ImmutablesLib.sol`
- Entrypoint: `EscrowDst.publicWithdraw(bytes32,IBaseEscrow.Immutables)`
- Attacker controls: public execution timing, the revealed secret, access-token ownership, and the fee blob chosen at destination creation
- Exploit idea: Probe whether the public settle path is safe under hostile fee metadata.
- Invariant to test: Public settlement must preserve the same amount and fee invariants as the private destination withdrawal.
- Expected Immunefi impact: Protocol insolvency
- Fast validation: With a funded destination escrow where the protocol fee alone is near the full amount, call `publicWithdraw()` at in the same block as the first destination action and verify that the maker, protocol, and integrator balances stay consistent.
