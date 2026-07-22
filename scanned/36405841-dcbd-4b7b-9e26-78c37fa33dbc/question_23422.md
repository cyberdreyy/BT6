Q23422: attribution bypass in providers-oracle attributed read path when the feed uses a packed spread or codebook boundary value near the sentinel representation

Question
Can an unprivileged attacker enter through `metric-core/contracts/MetricOmmPool.sol::swap` with public oracle registration that later enables pool reads while the feed uses a packed spread or codebook boundary value near the sentinel representation, so that the attributed providers-oracle read path can be reached from a pool or provider context that should have been rejected along `pool.swap -> provider.getBidAndAskPrice -> oracle.price(feedId, pool) -> inSwap and registration checks`, corrupting pool attribution, `inSwap()` binding, blacklist state, and the exact feed authorization checked before a live read? The trader controls the public swap timing; if read attribution is fragile, a production pool can consume a quote it was not supposed to be authorized to read. Trigger a public swap that arranges `inSwap()` and provider calls in a way the oracle misattributes.

Target
- File/function: smart-contracts-poc/contracts/oracles/providers/OracleBase.sol::price
- Entrypoint: metric-core/contracts/MetricOmmPool.sol::swap
- Attacker controls: public oracle registration that later enables pool reads
- Exploit idea: Reach `pool.swap -> provider.getBidAndAskPrice -> oracle.price(feedId, pool) -> inSwap and registration checks` in a live public flow and show that trigger a public swap that arranges `inswap()` and provider calls in a way the oracle misattributes. The exact value at risk is pool attribution, `inSwap()` binding, blacklist state, and the exact feed authorization checked before a live read.
- Invariant to test: Attributed oracle reads must be bound to the exact pool/provider pair that the live swap path intended to authorize. The concrete assertion should cover pool attribution, `inSwap()` binding, blacklist state, and the exact feed authorization checked before a live read.
- Expected Immunefi impact: High if the wrong pool can consume a live quote from a feed it should not be allowed to read.
- Fast validation: Stress `inSwap()` attribution and registration boundaries through real public swaps and assert the oracle never returns a live quote to the wrong pool/provider pair.
