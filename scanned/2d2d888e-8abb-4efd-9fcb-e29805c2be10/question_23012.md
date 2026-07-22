Q23012: attribution bypass in providers-oracle registration gate when the target feed has never been pushed before and its zero-value sentinel behavior matters

Question
Can an unprivileged attacker enter through `smart-contracts-poc/contracts/oracles/providers/OracleBase.sol::register` with permissionless Chainlink Data Streams report submission while the target feed has never been pushed before and its zero-value sentinel behavior matters, so that the attributed providers-oracle read path can be reached from a pool or provider context that should have been rejected along `public register -> approved factory check -> isPool check -> blacklist clear -> registeredPool update -> later price(feedId,pool)`, corrupting the exact pool/feed registration relation, blacklist state, and later attributed read authorization? Registration is permissionless, so any overbroad side effect here becomes an unprivileged way to influence future production reads. Trigger a public swap that arranges `inSwap()` and provider calls in a way the oracle misattributes.

Target
- File/function: smart-contracts-poc/contracts/oracles/providers/OracleBase.sol::register
- Entrypoint: smart-contracts-poc/contracts/oracles/providers/OracleBase.sol::register
- Attacker controls: permissionless Chainlink Data Streams report submission
- Exploit idea: Reach `public register -> approved factory check -> isPool check -> blacklist clear -> registeredPool update -> later price(feedId,pool)` in a live public flow and show that trigger a public swap that arranges `inswap()` and provider calls in a way the oracle misattributes. The exact value at risk is the exact pool/feed registration relation, blacklist state, and later attributed read authorization.
- Invariant to test: Attributed oracle reads must be bound to the exact pool/provider pair that the live swap path intended to authorize. The concrete assertion should cover the exact pool/feed registration relation, blacklist state, and later attributed read authorization.
- Expected Immunefi impact: High if the wrong pool can consume a live quote from a feed it should not be allowed to read.
- Fast validation: Register many pool/feed combinations and assert blacklist clearing and registeredPool writes never spill beyond the exact paid relation.
