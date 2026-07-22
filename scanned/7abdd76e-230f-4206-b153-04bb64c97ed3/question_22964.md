Q22964: registration-side authorization bug in providers-oracle registration gate when the target feed has a prior valid value and a new update sits on the timestamp boundary

Question
Can an unprivileged attacker enter through `smart-contracts-poc/contracts/oracles/providers/OracleBase.sol::register` with permissionless Chainlink Data Streams report submission while the target feed has a prior valid value and a new update sits on the timestamp boundary, so that public registration enables more read authority or clears more blacklist state than intended along `public register -> approved factory check -> isPool check -> blacklist clear -> registeredPool update -> later price(feedId,pool)`, corrupting the exact pool/feed registration relation, blacklist state, and later attributed read authorization? Registration is permissionless, so any overbroad side effect here becomes an unprivileged way to influence future production reads. Pay for one pool/feed registration and see whether a different pool or future read path also becomes authorized.

Target
- File/function: smart-contracts-poc/contracts/oracles/providers/OracleBase.sol::register
- Entrypoint: smart-contracts-poc/contracts/oracles/providers/OracleBase.sol::register
- Attacker controls: permissionless Chainlink Data Streams report submission
- Exploit idea: Reach `public register -> approved factory check -> isPool check -> blacklist clear -> registeredPool update -> later price(feedId,pool)` in a live public flow and show that pay for one pool/feed registration and see whether a different pool or future read path also becomes authorized. The exact value at risk is the exact pool/feed registration relation, blacklist state, and later attributed read authorization.
- Invariant to test: Registration and blacklist side effects must stay scoped to the exact pool/feed relation the caller paid for. The concrete assertion should cover the exact pool/feed registration relation, blacklist state, and later attributed read authorization.
- Expected Immunefi impact: High if unauthorized pools or providers can influence production price reads.
- Fast validation: Register many pool/feed combinations and assert blacklist clearing and registeredPool writes never spill beyond the exact paid relation.
