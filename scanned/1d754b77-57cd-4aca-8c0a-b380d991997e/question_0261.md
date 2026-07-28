# Q261: One-wei-below boundary can skip into band 60

## Question
Can an unprivileged filler choose a first partial fill at `(floor(orderMakingAmount * 61 / 100) - 1 wei)` and pair it with a proof for band `60` or `59` so that `_isValidPartialFill()` accepts the wrong side of the 60% to 61% boundary, allowing secret reuse or causing a legitimate fill to freeze on an off-by-one edge?

## Target
- File/function: `contracts/MerkleStorageInvalidator.sol::takerInteraction`, `contracts/BaseEscrowFactory.sol::_postInteraction`, `contracts/BaseEscrowFactory.sol::_isValidPartialFill`
- Entrypoint: `LimitOrderProtocol.fillOrderArgs(...)` -> taker interaction -> postInteraction
- Attacker controls: `makingAmount = floor(orderMakingAmount * 61 / 100) - 1`, adjacent Merkle proof/index pairs, and a multiple-fill order
- Exploit idea: Probe the off-by-one boundary immediately below an index transition.
- Invariant to test: Amounts below a boundary must not consume the next band secret, and amounts above it must not reuse the previous band secret.
- Expected Immunefi impact: Temporary freezing of funds
- Fast validation: Submit first fills at one wei below `floor(orderMakingAmount * 61 / 100)` using both adjacent proof indexes and check whether any unauthorized band is accepted.
