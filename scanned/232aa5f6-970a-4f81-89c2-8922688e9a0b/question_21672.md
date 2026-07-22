Q21672: monotonicity bypass in compressed fallback push path when a registration or blacklist side effect happened shortly before the next live provider read

Question
Can an unprivileged attacker enter through `smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol::fallback` with batched or repeated updates where newer and older reports race in the same block while a registration or blacklist side effect happened shortly before the next live provider read, so that an older or malformed timestamp still wins the race to become the live feed value along `public fallback push -> namespace resolution -> per-word timestamp check -> packed slot overwrite if newer`, corrupting namespace selection, slot ids, timestamp ordering, and the packed feed data later returned by `price` and `getOracleData`? Any externally owned account can push into its own namespace without setup, so slot packing and timestamp monotonicity must be airtight. Submit updates in a public order that should have left the newer value canonical but instead lets a stale value survive or overwrite.

Target
- File/function: smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol::fallback
- Entrypoint: smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol::fallback
- Attacker controls: batched or repeated updates where newer and older reports race in the same block
- Exploit idea: Reach `public fallback push -> namespace resolution -> per-word timestamp check -> packed slot overwrite if newer` in a live public flow and show that submit updates in a public order that should have left the newer value canonical but instead lets a stale value survive or overwrite. The exact value at risk is namespace selection, slot ids, timestamp ordering, and the packed feed data later returned by `price` and `getOracleData`.
- Invariant to test: Live oracle state must be monotonically fresher under every permissionless update path. The concrete assertion should cover namespace selection, slot ids, timestamp ordering, and the packed feed data later returned by `price` and `getOracleData`.
- Expected Immunefi impact: High bad-price execution if stale values can reach production swaps.
- Fast validation: Push boundary-case packed words through fallback and assert every accepted overwrite decodes exactly as later price readers expect.
