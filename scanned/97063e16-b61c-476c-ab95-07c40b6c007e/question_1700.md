# Q1700: Src publicCancel replay after native top-up cross_chain_delay mixed

## Question
After one successful `EscrowSrc.publicCancel()`, can any later access-token holder reuse the same clone at after cross-chain execution has stalled but before rescue and sweep another `safetyDeposit` from a later native-token top-up of `amount + safetyDeposit` worth of balances because the public cancel path never transitions the escrow into a terminal state?

## Target
- File/function: `contracts/EscrowSrc.sol::publicCancel`
- Entrypoint: `EscrowSrc.publicCancel(IBaseEscrow.Immutables)`
- Attacker controls: access-token ownership, timing after `SrcPublicCancellation`, and any later native token added to the clone
- Exploit idea: Replay the public cancel path to collect repeated safety-deposit refunds from the same clone.
- Invariant to test: A source escrow that has already been publicly canceled must not pay another safety-deposit refund.
- Expected Immunefi impact: Direct theft of any user funds, whether at-rest or in-motion
- Fast validation: Public-cancel a source escrow, send `amount + safetyDeposit` worth of balances of native token to it, use another access-token holder to call `publicCancel()` again, and observe whether the second refund succeeds.
