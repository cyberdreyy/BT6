Q22882: packed-slot decode confusion in providers-oracle registration gate when the target feed has a prior valid value and a new update sits on the timestamp boundary

Question
Can an unprivileged attacker enter through `smart-contracts-poc/contracts/oracles/providers/OracleBase.sol::register` with permissionless compressed-oracle signed slot updates while the target feed has a prior valid value and a new update sits on the timestamp boundary, so that slot packing, sentinel markers, or codebook boundaries decode into a valid-looking price or spread that should have failed closed along `public register -> approved factory check -> isPool check -> blacklist clear -> registeredPool update -> later price(feedId,pool)`, corrupting the exact pool/feed registration relation, blacklist state, and later attributed read authorization? Registration is permissionless, so any overbroad side effect here becomes an unprivileged way to influence future production reads. Push or read a boundary compressed value whose spread or timestamp representation is easy to misinterpret.

Target
- File/function: smart-contracts-poc/contracts/oracles/providers/OracleBase.sol::register
- Entrypoint: smart-contracts-poc/contracts/oracles/providers/OracleBase.sol::register
- Attacker controls: permissionless compressed-oracle signed slot updates
- Exploit idea: Reach `public register -> approved factory check -> isPool check -> blacklist clear -> registeredPool update -> later price(feedId,pool)` in a live public flow and show that push or read a boundary compressed value whose spread or timestamp representation is easy to misinterpret. The exact value at risk is the exact pool/feed registration relation, blacklist state, and later attributed read authorization.
- Invariant to test: Packed compressed data must decode unambiguously and reject every sentinel or malformed boundary state before price consumers trust it. The concrete assertion should cover the exact pool/feed registration relation, blacklist state, and later attributed read authorization.
- Expected Immunefi impact: Critical if a malformed compressed value can drive live pool pricing.
- Fast validation: Register many pool/feed combinations and assert blacklist clearing and registeredPool writes never spill beyond the exact paid relation.
