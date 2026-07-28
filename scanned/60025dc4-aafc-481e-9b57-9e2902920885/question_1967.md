# Q1967: Src publicWithdraw reentrancy can amplify refill just_before_rescue full

## Question
Can an access-token-holder contract call `EscrowSrc.publicWithdraw()` just before `RESCUE_DELAY`, receive the native safety-deposit refund in its fallback, and reenter the same source clone to claim a later maker-token refill of `amount` or any other payable balance before the first public-withdrawal call finishes?

## Target
- File/function: `contracts/EscrowSrc.sol::publicWithdraw`
- Entrypoint: `EscrowSrc.publicWithdraw(bytes32,IBaseEscrow.Immutables)` from a contract caller
- Attacker controls: a contract that holds the access token, the secret, fallback reentrancy, and any later inbound maker-token balance
- Exploit idea: Exploit the payable refund at the end of the public withdraw path to reenter the same clone.
- Invariant to test: The public withdrawal path must remain single-use even when the refund recipient can reenter.
- Expected Immunefi impact: Direct theft of any user funds, whether at-rest or in-motion
- Fast validation: Grant the access token to a reentering contract, top up the source clone with `amount` during the window, and verify that fallback reentry cannot extract extra value.
