Q20748: zero-state fail-open in compressed contract-pusher delegation when a registration or blacklist side effect happened shortly before the next live provider read

Question
Can an unprivileged attacker enter through `smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol::allowContractPushers` with permissionless Chainlink Data Streams report submission while a registration or blacklist side effect happened shortly before the next live provider read, so that an uninitialized or zero-value feed state later looks like a valid quote instead of a halt condition along `public allowContractPushers -> staticcall isPusher(creator) -> namespaceRemapping update`, corrupting who is treated as an authorized contract pusher and which namespace their later fallback writes can mutate? This path trusts a live contract response instead of a signature, so any ambiguity in that trust boundary is publicly reachable. Read or route through a feed that has never been safely initialized and look for a valid-looking price path anyway.

Target
- File/function: smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol::allowContractPushers
- Entrypoint: smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol::allowContractPushers
- Attacker controls: permissionless Chainlink Data Streams report submission
- Exploit idea: Reach `public allowContractPushers -> staticcall isPusher(creator) -> namespaceRemapping update` in a live public flow and show that read or route through a feed that has never been safely initialized and look for a valid-looking price path anyway. The exact value at risk is who is treated as an authorized contract pusher and which namespace their later fallback writes can mutate.
- Invariant to test: Never-pushed or zero-state feeds must fail closed before any provider or pool can consume them. The concrete assertion should cover who is treated as an authorized contract pusher and which namespace their later fallback writes can mutate.
- Expected Immunefi impact: High if uninitialized feeds can still drive live swap pricing.
- Fast validation: Use varied contract-pusher behaviors and assert delegation only succeeds for the exact creator/contract relation the oracle intended to trust.
