Q21819: attribution bypass in compressed fallback push path when the feed uses a packed spread or codebook boundary value near the sentinel representation

Question
Can an unprivileged attacker enter through `smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol::fallback` with permissionless compressed-oracle batch fallback pushes while the feed uses a packed spread or codebook boundary value near the sentinel representation, so that the attributed providers-oracle read path can be reached from a pool or provider context that should have been rejected along `public fallback push -> namespace resolution -> per-word timestamp check -> packed slot overwrite if newer`, corrupting namespace selection, slot ids, timestamp ordering, and the packed feed data later returned by `price` and `getOracleData`? Any externally owned account can push into its own namespace without setup, so slot packing and timestamp monotonicity must be airtight. Trigger a public swap that arranges `inSwap()` and provider calls in a way the oracle misattributes.

Target
- File/function: smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol::fallback
- Entrypoint: smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol::fallback
- Attacker controls: permissionless compressed-oracle batch fallback pushes
- Exploit idea: Reach `public fallback push -> namespace resolution -> per-word timestamp check -> packed slot overwrite if newer` in a live public flow and show that trigger a public swap that arranges `inswap()` and provider calls in a way the oracle misattributes. The exact value at risk is namespace selection, slot ids, timestamp ordering, and the packed feed data later returned by `price` and `getOracleData`.
- Invariant to test: Attributed oracle reads must be bound to the exact pool/provider pair that the live swap path intended to authorize. The concrete assertion should cover namespace selection, slot ids, timestamp ordering, and the packed feed data later returned by `price` and `getOracleData`.
- Expected Immunefi impact: High if the wrong pool can consume a live quote from a feed it should not be allowed to read.
- Fast validation: Push boundary-case packed words through fallback and assert every accepted overwrite decodes exactly as later price readers expect.
