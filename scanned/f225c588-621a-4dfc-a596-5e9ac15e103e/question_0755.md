# Q755: Taking-amount settlement path can misbind band 54

## Question
Can an unprivileged filler use the `takingAmount` path in `LimitOrderProtocol.fillOrderArgs()` so that settlement-side rounding changes the effective source `makingAmount`, but `_isValidPartialFill()` still accepts band `54` as if the maker-amount path had been used, leading to a secret/index mismatch that creates an undercollateralized or replayable source escrow?

## Target
- File/function: `contracts/MerkleStorageInvalidator.sol::takerInteraction`, `contracts/BaseEscrowFactory.sol::_postInteraction`, `contracts/BaseEscrowFactory.sol::_isValidPartialFill`
- Entrypoint: `LimitOrderProtocol.fillOrderArgs(...)` -> taker interaction -> postInteraction
- Attacker controls: `takingAmount`-driven fill selection, rate-bump rounding, Merkle proof for band `54`, and order args
- Exploit idea: Check whether maker-vs-taker amount conversion can desynchronize the validated band.
- Invariant to test: The Merkle band consumed for a fill must match the actual deployed source amount after settlement-side rounding.
- Expected Immunefi impact: Protocol insolvency
- Fast validation: Replicate the `takingAmount` fill path with band `54`, compare the corrected source amount against the validated index, and assert that mismatched bands cannot pass.
