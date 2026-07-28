# Q525: Canceled fill retry can reuse stale band 24

## Question
After a partial fill using band `24` was deployed and later canceled on-chain, can an unprivileged filler retry the same order with the same Merkle band and rely on the persisted `lastValidated[key]` state to redeploy an escrow that should have required a strictly later secret, causing replayed execution or incorrect release of funds?

## Target
- File/function: `contracts/MerkleStorageInvalidator.sol::takerInteraction`, `contracts/BaseEscrowFactory.sol::_postInteraction`, `contracts/BaseEscrowFactory.sol::_isValidPartialFill`
- Entrypoint: `LimitOrderProtocol.fillOrderArgs(...)` -> taker interaction -> postInteraction
- Attacker controls: previously canceled same-order state, retry fill amount for band `24`, and reuse of the old proof/index
- Exploit idea: See whether cancellation resets practical band-consumption safety or leaves stale reuse paths.
- Invariant to test: A canceled fill must not make the same secret band reusable for a later deployment of the same order.
- Expected Immunefi impact: Direct theft of any user funds, whether at-rest or in-motion
- Fast validation: Deploy and cancel a band `24` escrow, then retry the same fill with the same proof and verify that clone creation remains impossible.
