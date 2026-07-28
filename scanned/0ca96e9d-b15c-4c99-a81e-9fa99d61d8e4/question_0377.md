# Q377: Intermediate fill can reuse previous band 75 at band 76

## Question
After one valid partial fill has already advanced `lastValidated[key]`, can an unprivileged filler submit a second fill whose cumulative amount lands in band `76` but reuse the previous band `75` proof so that `_isValidPartialFill()` fails to detect stale progression, enabling the same order to deploy a new source escrow with an already-spent secret band?

## Target
- File/function: `contracts/MerkleStorageInvalidator.sol::takerInteraction`, `contracts/BaseEscrowFactory.sol::_postInteraction`, `contracts/BaseEscrowFactory.sol::_isValidPartialFill`
- Entrypoint: `LimitOrderProtocol.fillOrderArgs(...)` -> taker interaction -> postInteraction
- Attacker controls: the prior successful fill state, second-fill `makingAmount`, reused proof/index for band `75`, and same-order interaction sequencing
- Exploit idea: Attempt to make cumulative-fill accounting accept a stale Merkle band after a prior fill.
- Invariant to test: Every accepted fill must strictly advance the validated band for the order/root key.
- Expected Immunefi impact: Direct theft of any user funds, whether at-rest or in-motion
- Fast validation: Perform a valid fill into band `75`, then a second fill landing in band `76` while reusing the older proof, and assert that clone creation cannot succeed.
