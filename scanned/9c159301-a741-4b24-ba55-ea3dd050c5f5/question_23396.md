Q23396: registration-side authorization bug in providers-oracle attributed read path when multiple reports for the same feed arrive in different orders within one transaction or block

Question
Can an unprivileged attacker enter through `metric-core/contracts/MetricOmmPool.sol::swap` with permissionless Chainlink Data Streams report submission while multiple reports for the same feed arrive in different orders within one transaction or block, so that public registration enables more read authority or clears more blacklist state than intended along `pool.swap -> provider.getBidAndAskPrice -> oracle.price(feedId, pool) -> inSwap and registration checks`, corrupting pool attribution, `inSwap()` binding, blacklist state, and the exact feed authorization checked before a live read? The trader controls the public swap timing; if read attribution is fragile, a production pool can consume a quote it was not supposed to be authorized to read. Pay for one pool/feed registration and see whether a different pool or future read path also becomes authorized.

Target
- File/function: smart-contracts-poc/contracts/oracles/providers/OracleBase.sol::price
- Entrypoint: metric-core/contracts/MetricOmmPool.sol::swap
- Attacker controls: permissionless Chainlink Data Streams report submission
- Exploit idea: Reach `pool.swap -> provider.getBidAndAskPrice -> oracle.price(feedId, pool) -> inSwap and registration checks` in a live public flow and show that pay for one pool/feed registration and see whether a different pool or future read path also becomes authorized. The exact value at risk is pool attribution, `inSwap()` binding, blacklist state, and the exact feed authorization checked before a live read.
- Invariant to test: Registration and blacklist side effects must stay scoped to the exact pool/feed relation the caller paid for. The concrete assertion should cover pool attribution, `inSwap()` binding, blacklist state, and the exact feed authorization checked before a live read.
- Expected Immunefi impact: High if unauthorized pools or providers can influence production price reads.
- Fast validation: Stress `inSwap()` attribution and registration boundaries through real public swaps and assert the oracle never returns a live quote to the wrong pool/provider pair.
