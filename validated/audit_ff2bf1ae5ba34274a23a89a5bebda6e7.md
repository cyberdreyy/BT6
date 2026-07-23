I was unable to read the actual file contents during this investigation — I only reached the directory structure level before hitting the tool call limit. Without reading the source of files like `smart-contracts-poc/contracts/oracles/compressed/`, `providers/`, the pusher delegation logic, `AnchoredPriceProvider.sol`, `PriceProvider.sol`, `ProtectedPriceProvider.sol`, and the core pool's price consumption path, I cannot trace a root cause, identify a corrupted value, or confirm a broken invariant.

Fabricating a finding from directory names alone would be irresponsible and violates the required work plan (steps 3–5 require tracing attacker input through production validation and identifying the exact wrong value).

**I cannot produce a valid output under the strict format rules without that code-level evidence.**

To complete this analysis properly, a full session is needed to read:
- `smart-contracts-poc/contracts/oracles/compressed/` — compression/codebook path
- `smart-contracts-poc/contracts/oracles/providers/` — Chainlink/Pyth provider bindings
- `smart-contracts-poc/contracts/AnchoredPriceProvider.sol` — anchored price consumption
- `smart-contracts-poc/contracts/PriceProvider.sol` / `PriceProviderL2.sol` — pusher delegation and feed authorization
- `metric-core/contracts/libraries/SwapMath.sol` and `Slot0Library.sol` — how prices reach swaps
- `metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol` and `PriceVelocityGuardExtension.sol` — abuse-protection guards