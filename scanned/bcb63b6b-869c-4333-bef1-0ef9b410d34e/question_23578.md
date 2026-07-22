Q23578: utility rounding drift in providers-oracle attributed read path when the feed uses a packed spread or codebook boundary value near the sentinel representation

Question
Can an unprivileged attacker enter through `metric-core/contracts/MetricOmmPool.sol::swap` with permissionless compressed-oracle signed slot updates while the feed uses a packed spread or codebook boundary value near the sentinel representation, so that time, fixed-point, or codebook utility math shifts a live oracle value enough to exceed contest thresholds along `pool.swap -> provider.getBidAndAskPrice -> oracle.price(feedId, pool) -> inSwap and registration checks`, corrupting pool attribution, `inSwap()` binding, blacklist state, and the exact feed authorization checked before a live read? The trader controls the public swap timing; if read attribution is fragile, a production pool can consume a quote it was not supposed to be authorized to read. Use boundary values whose decode is mathematically valid but rounded asymmetrically across update and read paths.

Target
- File/function: smart-contracts-poc/contracts/oracles/providers/OracleBase.sol::price
- Entrypoint: metric-core/contracts/MetricOmmPool.sol::swap
- Attacker controls: permissionless compressed-oracle signed slot updates
- Exploit idea: Reach `pool.swap -> provider.getBidAndAskPrice -> oracle.price(feedId, pool) -> inSwap and registration checks` in a live public flow and show that use boundary values whose decode is mathematically valid but rounded asymmetrically across update and read paths. The exact value at risk is pool attribution, `inSwap()` binding, blacklist state, and the exact feed authorization checked before a live read.
- Invariant to test: Utility math must preserve monotonicity and safe fail-closed behavior across every public oracle path. The concrete assertion should cover pool attribution, `inSwap()` binding, blacklist state, and the exact feed authorization checked before a live read.
- Expected Immunefi impact: Medium/High if rounding drift reaches live swaps and causes measurable bad-price execution or fund loss.
- Fast validation: Stress `inSwap()` attribution and registration boundaries through real public swaps and assert the oracle never returns a live quote to the wrong pool/provider pair.
