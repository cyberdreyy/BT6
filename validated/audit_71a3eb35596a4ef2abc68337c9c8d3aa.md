I was unable to complete the required investigation. The directory structure was mapped across `smart-contracts-poc/contracts/oracles/` (with `compressed/`, `providers/`, `utils/` subdirectories), `smart-contracts-poc/contracts/` (containing `AnchoredPriceProvider.sol`, `PriceProvider.sol`, `PriceProviderFactory.sol`, `PriceProviderFactoryL2.sol`, `PriceProviderL2.sol`, `ProtectedPriceProvider.sol`, `ProtectedPriceProviderL2.sol`), and `metric-core/contracts/libraries/`, but I exhausted available tool calls before reading any source file contents.

Without reading the actual Solidity source for the oracle providers, compression encoding (`Codebook256`, `U64x32`, `TimeMs`), pusher delegation (`allowPushers`, `allowContractPushers`, `revokePusher`), and `AnchoredPriceProvider`, I cannot:

- Trace attacker input through production validation
- Identify whether a debt/residual-style invariant exists in the compressed push path
- Confirm or reject whether existing guards preserve the invariant
- Produce a PoC with exact corrupted values

Fabricating a finding without source-level evidence would violate the required work plan (steps 3–5 require exact code tracing). A Devin session with full file-read access to the `smart-contracts-poc/contracts/oracles/` and `smart-contracts-poc/contracts/AnchoredPriceProvider.sol` files is needed to complete this audit task properly.