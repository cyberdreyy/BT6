# Q1782: Src publicCancel reentrancy can amplify native refill next_block_replay tiny

## Question
Can an access-token-holder contract call `EscrowSrc.publicCancel()` at in the next block after the first lifecycle call, receive the native refund in its fallback, and reenter the same clone while another native balance of `amount / 100` is present, collecting repeated public-cancel refunds from one source escrow?

## Target
- File/function: `contracts/EscrowSrc.sol::publicCancel`
- Entrypoint: `EscrowSrc.publicCancel(IBaseEscrow.Immutables)` from a contract caller
- Attacker controls: a contract caller that holds the access token, public-cancel timing, and later native balances added to the clone
- Exploit idea: Exploit the public-cancel refund callback to replay the same cancel payout path reentrantly.
- Invariant to test: A publicly canceled source escrow must not expose repeated refunds through fallback reentry.
- Expected Immunefi impact: Direct theft of any user funds, whether at-rest or in-motion
- Fast validation: Grant the access token to a reentering contract, keep `amount / 100` of native balance on the clone, call `publicCancel()`, and inspect whether fallback reentry can extract more than one refund.
