# Q642: Final fill can accept non-final secret at band 41

## Question
When a multiple-fill order is completed on its last fill, can an unprivileged filler choose a final `makingAmount` that should require the dedicated completion secret but still pass `_isValidPartialFill()` with band `41` instead of the terminal `N` secret, letting the order settle with a non-final hashlock and enabling replay or theft on the source or destination side?

## Target
- File/function: `contracts/MerkleStorageInvalidator.sol::takerInteraction`, `contracts/BaseEscrowFactory.sol::_postInteraction`, `contracts/BaseEscrowFactory.sol::_isValidPartialFill`
- Entrypoint: `LimitOrderProtocol.fillOrderArgs(...)` -> taker interaction -> postInteraction
- Attacker controls: final-fill amount selection, band `41` proof, and the last fill's transaction ordering
- Exploit idea: Probe the `remainingMakingAmount == makingAmount` branch that requires the extra final secret.
- Invariant to test: A fully completed order must consume the dedicated final secret, not any earlier partial-fill secret.
- Expected Immunefi impact: Direct theft of any user funds, whether at-rest or in-motion
- Fast validation: Drive an order to its last fill, try to finish it with a non-final proof, and assert that no source clone can be deployed or withdrawn with that earlier secret.
