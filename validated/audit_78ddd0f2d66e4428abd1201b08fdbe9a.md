Looking at the full oracle infrastructure, I need to trace through all the key paths: `CompressedOracle.sol` (fallback, `updateBySignature`, `allowPushers`, `allowContractPushers`), `OracleBase.sol` (providers), `ChainlinkOracle.sol`, `PythOracle.sol`, `Codebook256`, `U64x32`, and `TimeMs`.

Let me verify the remaining provider path and AnchoredPriceProvider: