# Q2643: Dst publicWithdraw fee boundary can mispay fees_exact late_public

## Question
At late in the public-withdrawal window, can a public access-token holder finalize a destination escrow whose `parameters` are crafted so that fees sum exactly to `immutables.amount`, causing `publicWithdraw()` to overpay fee recipients, underflow the maker payout, or leave the clone insolvent while still paying out the safety deposit?

## Target
- File/function: `contracts/EscrowDst.sol::publicWithdraw`, `contracts/libraries/ImmutablesLib.sol`
- Entrypoint: `EscrowDst.publicWithdraw(bytes32,IBaseEscrow.Immutables)`
- Attacker controls: public execution timing, the revealed secret, access-token ownership, and the fee blob chosen at destination creation
- Exploit idea: Probe whether the public settle path is safe under hostile fee metadata.
- Invariant to test: Public settlement must preserve the same amount and fee invariants as the private destination withdrawal.
- Expected Immunefi impact: Protocol insolvency
- Fast validation: With a funded destination escrow where fees sum exactly to `immutables.amount`, call `publicWithdraw()` at late in the public-withdrawal window and verify that the maker, protocol, and integrator balances stay consistent.
