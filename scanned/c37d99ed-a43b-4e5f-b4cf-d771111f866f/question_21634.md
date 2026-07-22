Q21634: signature replay or domain-separation bug in compressed fallback push path when multiple reports for the same feed arrive in different orders within one transaction or block

Question
Can an unprivileged attacker enter through `smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol::fallback` with permissionless compressed-oracle signed slot updates while multiple reports for the same feed arrive in different orders within one transaction or block, so that a valid signature for one context is also accepted in another context that changes who controls a namespace or feed update along `public fallback push -> namespace resolution -> per-word timestamp check -> packed slot overwrite if newer`, corrupting namespace selection, slot ids, timestamp ordering, and the packed feed data later returned by `price` and `getOracleData`? Any externally owned account can push into its own namespace without setup, so slot packing and timestamp monotonicity must be airtight. Reuse a public signature across creators, deadlines, contracts, or chain ids until the oracle accepts it outside its intended scope.

Target
- File/function: smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol::fallback
- Entrypoint: smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol::fallback
- Attacker controls: permissionless compressed-oracle signed slot updates
- Exploit idea: Reach `public fallback push -> namespace resolution -> per-word timestamp check -> packed slot overwrite if newer` in a live public flow and show that reuse a public signature across creators, deadlines, contracts, or chain ids until the oracle accepts it outside its intended scope. The exact value at risk is namespace selection, slot ids, timestamp ordering, and the packed feed data later returned by `price` and `getOracleData`.
- Invariant to test: Every signed oracle action must be bound to one exact creator, chain, contract, and action payload. The concrete assertion should cover namespace selection, slot ids, timestamp ordering, and the packed feed data later returned by `price` and `getOracleData`.
- Expected Immunefi impact: Critical if a public attacker can hijack update authority over a production feed namespace.
- Fast validation: Push boundary-case packed words through fallback and assert every accepted overwrite decodes exactly as later price readers expect.
