Q23250: monotonicity bypass in providers-oracle attributed read path when the target feed has never been pushed before and its zero-value sentinel behavior matters

Question
Can an unprivileged attacker enter through `metric-core/contracts/MetricOmmPool.sol::swap` with permissionless compressed-oracle signed slot updates while the target feed has never been pushed before and its zero-value sentinel behavior matters, so that an older or malformed timestamp still wins the race to become the live feed value along `pool.swap -> provider.getBidAndAskPrice -> oracle.price(feedId, pool) -> inSwap and registration checks`, corrupting pool attribution, `inSwap()` binding, blacklist state, and the exact feed authorization checked before a live read? The trader controls the public swap timing; if read attribution is fragile, a production pool can consume a quote it was not supposed to be authorized to read. Submit updates in a public order that should have left the newer value canonical but instead lets a stale value survive or overwrite.

Target
- File/function: smart-contracts-poc/contracts/oracles/providers/OracleBase.sol::price
- Entrypoint: metric-core/contracts/MetricOmmPool.sol::swap
- Attacker controls: permissionless compressed-oracle signed slot updates
- Exploit idea: Reach `pool.swap -> provider.getBidAndAskPrice -> oracle.price(feedId, pool) -> inSwap and registration checks` in a live public flow and show that submit updates in a public order that should have left the newer value canonical but instead lets a stale value survive or overwrite. The exact value at risk is pool attribution, `inSwap()` binding, blacklist state, and the exact feed authorization checked before a live read.
- Invariant to test: Live oracle state must be monotonically fresher under every permissionless update path. The concrete assertion should cover pool attribution, `inSwap()` binding, blacklist state, and the exact feed authorization checked before a live read.
- Expected Immunefi impact: High bad-price execution if stale values can reach production swaps.
- Fast validation: Stress `inSwap()` attribution and registration boundaries through real public swaps and assert the oracle never returns a live quote to the wrong pool/provider pair.
