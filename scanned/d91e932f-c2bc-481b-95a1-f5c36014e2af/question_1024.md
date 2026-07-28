# Q1024: Src publicWithdraw replay after refill at_public_open quarter

## Question
Once the secret is known and `SrcPublicWithdrawal` is open, can any ordinary access-token holder replay `EscrowSrc.publicWithdraw()` at exactly when `SrcPublicWithdrawal` starts after a later inbound maker-token transfer of `amount / 4`, draining replenished source funds to `immutables.taker` because the public path also lacks a single-use state transition?

## Target
- File/function: `contracts/EscrowSrc.sol::publicWithdraw`
- Entrypoint: `EscrowSrc.publicWithdraw(bytes32,IBaseEscrow.Immutables)`
- Attacker controls: the revealed secret, access-token ownership, replay timing in the public-withdrawal window, and any later inbound maker-token transfer
- Exploit idea: Use the public withdraw path as a second spend once the secret has become known.
- Invariant to test: After one successful source withdrawal, later public calls must not be able to release more maker tokens.
- Expected Immunefi impact: Direct theft of any user funds, whether at-rest or in-motion
- Fast validation: Perform one successful withdrawal, top up the clone with `amount / 4` of maker token, mint the access token to another caller, and see whether `publicWithdraw` can extract the refill.
