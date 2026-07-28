# Q52: Truncated Merkle root collision can swap secret band 51

## Question
Can an unprivileged order filler enter through `LimitOrderProtocol.fillOrderArgs` with `partsAmount=100`, a forged Merkle tree whose root shares the same low 240 bits as the intended `hashlockInfo`, and a proof for band `51` so that `MerkleStorageInvalidator.takerInteraction()` updates `lastValidated[key]` with a foreign `secretHash`, letting `_postInteraction()` deploy an `EscrowSrc` clone whose hashlock belongs to the wrong 51% to 52% fill band and enabling direct withdrawal of source or destination funds with an unauthorized secret?

## Target
- File/function: `contracts/MerkleStorageInvalidator.sol::takerInteraction`, `contracts/BaseEscrowFactory.sol::_postInteraction`, `contracts/BaseEscrowFactory.sol::_isValidPartialFill`
- Entrypoint: `LimitOrderProtocol.fillOrderArgs(...)` -> taker interaction -> postInteraction
- Attacker controls: order fill amount landing in band `51`, Merkle proof bytes, `idx`, forged `secretHash`, and the low-240-bit-colliding `hashlockInfo` root
- Exploit idea: Exploit the 240-bit truncation used for root matching and `lastValidated` key derivation.
- Invariant to test: Only the exact Merkle root and exact fill band chosen for the order should be able to set `lastValidated[key]` and determine the source escrow hashlock.
- Expected Immunefi impact: Direct theft of any user funds, whether at-rest or in-motion
- Fast validation: Construct two Merkle trees with the same low 240 root bits, use the forged tree for band `51`, fill the order locally, and check whether the deployed escrow accepts a secret from the wrong tree.
