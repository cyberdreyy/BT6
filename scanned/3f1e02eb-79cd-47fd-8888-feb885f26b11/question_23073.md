Q23073: schema or resolution mix-up in providers-oracle registration gate when multiple reports for the same feed arrive in different orders within one transaction or block

Question
Can an unprivileged attacker enter through `smart-contracts-poc/contracts/oracles/providers/OracleBase.sol::register` with permissionless compressed-oracle pusher delegation and revocation calls while multiple reports for the same feed arrive in different orders within one transaction or block, so that report version or timestamp-resolution dispatch decodes valid signed data under the wrong schema family along `public register -> approved factory check -> isPool check -> blacklist clear -> registeredPool update -> later price(feedId,pool)`, corrupting the exact pool/feed registration relation, blacklist state, and later attributed read authorization? Registration is permissionless, so any overbroad side effect here becomes an unprivileged way to influence future production reads. Submit a real report whose feed id sits near a schema or resolution boundary and see whether decode logic takes the wrong branch.

Target
- File/function: smart-contracts-poc/contracts/oracles/providers/OracleBase.sol::register
- Entrypoint: smart-contracts-poc/contracts/oracles/providers/OracleBase.sol::register
- Attacker controls: permissionless compressed-oracle pusher delegation and revocation calls
- Exploit idea: Reach `public register -> approved factory check -> isPool check -> blacklist clear -> registeredPool update -> later price(feedId,pool)` in a live public flow and show that submit a real report whose feed id sits near a schema or resolution boundary and see whether decode logic takes the wrong branch. The exact value at risk is the exact pool/feed registration relation, blacklist state, and later attributed read authorization.
- Invariant to test: Every verified report must be decoded by exactly the schema and time-resolution family it was signed for. The concrete assertion should cover the exact pool/feed registration relation, blacklist state, and later attributed read authorization.
- Expected Immunefi impact: High bad-price execution if normalized oracle data is wrong despite successful verification.
- Fast validation: Register many pool/feed combinations and assert blacklist clearing and registeredPool writes never spill beyond the exact paid relation.
