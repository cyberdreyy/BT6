Q23138: zero-state fail-open in providers-oracle registration gate when the feed uses a packed spread or codebook boundary value near the sentinel representation

Question
Can an unprivileged attacker enter through `smart-contracts-poc/contracts/oracles/providers/OracleBase.sol::register` with permissionless compressed-oracle signed slot updates while the feed uses a packed spread or codebook boundary value near the sentinel representation, so that an uninitialized or zero-value feed state later looks like a valid quote instead of a halt condition along `public register -> approved factory check -> isPool check -> blacklist clear -> registeredPool update -> later price(feedId,pool)`, corrupting the exact pool/feed registration relation, blacklist state, and later attributed read authorization? Registration is permissionless, so any overbroad side effect here becomes an unprivileged way to influence future production reads. Read or route through a feed that has never been safely initialized and look for a valid-looking price path anyway.

Target
- File/function: smart-contracts-poc/contracts/oracles/providers/OracleBase.sol::register
- Entrypoint: smart-contracts-poc/contracts/oracles/providers/OracleBase.sol::register
- Attacker controls: permissionless compressed-oracle signed slot updates
- Exploit idea: Reach `public register -> approved factory check -> isPool check -> blacklist clear -> registeredPool update -> later price(feedId,pool)` in a live public flow and show that read or route through a feed that has never been safely initialized and look for a valid-looking price path anyway. The exact value at risk is the exact pool/feed registration relation, blacklist state, and later attributed read authorization.
- Invariant to test: Never-pushed or zero-state feeds must fail closed before any provider or pool can consume them. The concrete assertion should cover the exact pool/feed registration relation, blacklist state, and later attributed read authorization.
- Expected Immunefi impact: High if uninitialized feeds can still drive live swap pricing.
- Fast validation: Register many pool/feed combinations and assert blacklist clearing and registeredPool writes never spill beyond the exact paid relation.
