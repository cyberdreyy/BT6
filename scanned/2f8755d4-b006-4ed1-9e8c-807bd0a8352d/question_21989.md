Q21989: utility rounding drift in compressed fallback push path when a registration or blacklist side effect happened shortly before the next live provider read

Question
Can an unprivileged attacker enter through `smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol::fallback` with permissionless Pyth Lazer fallback payload submission while a registration or blacklist side effect happened shortly before the next live provider read, so that time, fixed-point, or codebook utility math shifts a live oracle value enough to exceed contest thresholds along `public fallback push -> namespace resolution -> per-word timestamp check -> packed slot overwrite if newer`, corrupting namespace selection, slot ids, timestamp ordering, and the packed feed data later returned by `price` and `getOracleData`? Any externally owned account can push into its own namespace without setup, so slot packing and timestamp monotonicity must be airtight. Use boundary values whose decode is mathematically valid but rounded asymmetrically across update and read paths.

Target
- File/function: smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol::fallback
- Entrypoint: smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol::fallback
- Attacker controls: permissionless Pyth Lazer fallback payload submission
- Exploit idea: Reach `public fallback push -> namespace resolution -> per-word timestamp check -> packed slot overwrite if newer` in a live public flow and show that use boundary values whose decode is mathematically valid but rounded asymmetrically across update and read paths. The exact value at risk is namespace selection, slot ids, timestamp ordering, and the packed feed data later returned by `price` and `getOracleData`.
- Invariant to test: Utility math must preserve monotonicity and safe fail-closed behavior across every public oracle path. The concrete assertion should cover namespace selection, slot ids, timestamp ordering, and the packed feed data later returned by `price` and `getOracleData`.
- Expected Immunefi impact: Medium/High if rounding drift reaches live swaps and causes measurable bad-price execution or fund loss.
- Fast validation: Push boundary-case packed words through fallback and assert every accepted overwrite decodes exactly as later price readers expect.
