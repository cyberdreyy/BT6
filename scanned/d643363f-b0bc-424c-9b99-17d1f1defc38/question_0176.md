# Q176: Exact upper-bound first fill can misclassify band 75

## Question
Can an unprivileged filler use a first partial fill whose `makingAmount` is exactly `floor(orderMakingAmount * 76 / 100)` and pair it with a Merkle proof for the wrong band so that `_isValidPartialFill()` accepts or rejects the `75% to 76%` boundary incorrectly, letting the protocol either reuse a stale secret band for theft or permanently freeze a legitimate first fill?

## Target
- File/function: `contracts/MerkleStorageInvalidator.sol::takerInteraction`, `contracts/BaseEscrowFactory.sol::_postInteraction`, `contracts/BaseEscrowFactory.sol::_isValidPartialFill`
- Entrypoint: `LimitOrderProtocol.fillOrderArgs(...)` -> taker interaction -> postInteraction
- Attacker controls: `makingAmount = floor(orderMakingAmount * 76 / 100)`, the first-fill proof/index pair, and order interaction bytes
- Exploit idea: Probe the exact upper boundary where `calculatedIndex` changes on the first fill.
- Invariant to test: At a first-fill boundary, only the unique Merkle index corresponding to the exact cumulative fill band should be accepted.
- Expected Immunefi impact: Direct theft of any user funds, whether at-rest or in-motion
- Fast validation: On a local fork, submit first fills at exactly `floor(orderMakingAmount * 76 / 100)` with adjacent proof indexes and assert that only the correct band deploys a source escrow.
