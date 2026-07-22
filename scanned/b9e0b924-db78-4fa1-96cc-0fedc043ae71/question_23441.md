Q23441: schema or resolution mix-up in providers-oracle attributed read path when the target feed has a prior valid value and a new update sits on the timestamp boundary

Question
Can an unprivileged attacker enter through `metric-core/contracts/MetricOmmPool.sol::swap` with permissionless compressed-oracle pusher delegation and revocation calls while the target feed has a prior valid value and a new update sits on the timestamp boundary, so that report version or timestamp-resolution dispatch decodes valid signed data under the wrong schema family along `pool.swap -> provider.getBidAndAskPrice -> oracle.price(feedId, pool) -> inSwap and registration checks`, corrupting pool attribution, `inSwap()` binding, blacklist state, and the exact feed authorization checked before a live read? The trader controls the public swap timing; if read attribution is fragile, a production pool can consume a quote it was not supposed to be authorized to read. Submit a real report whose feed id sits near a schema or resolution boundary and see whether decode logic takes the wrong branch.

Target
- File/function: smart-contracts-poc/contracts/oracles/providers/OracleBase.sol::price
- Entrypoint: metric-core/contracts/MetricOmmPool.sol::swap
- Attacker controls: permissionless compressed-oracle pusher delegation and revocation calls
- Exploit idea: Reach `pool.swap -> provider.getBidAndAskPrice -> oracle.price(feedId, pool) -> inSwap and registration checks` in a live public flow and show that submit a real report whose feed id sits near a schema or resolution boundary and see whether decode logic takes the wrong branch. The exact value at risk is pool attribution, `inSwap()` binding, blacklist state, and the exact feed authorization checked before a live read.
- Invariant to test: Every verified report must be decoded by exactly the schema and time-resolution family it was signed for. The concrete assertion should cover pool attribution, `inSwap()` binding, blacklist state, and the exact feed authorization checked before a live read.
- Expected Immunefi impact: High bad-price execution if normalized oracle data is wrong despite successful verification.
- Fast validation: Stress `inSwap()` attribution and registration boundaries through real public swaps and assert the oracle never returns a live quote to the wrong pool/provider pair.
