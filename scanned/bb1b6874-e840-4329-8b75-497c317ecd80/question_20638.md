Q20638: attribution bypass in compressed contract-pusher delegation when multiple reports for the same feed arrive in different orders within one transaction or block

Question
Can an unprivileged attacker enter through `smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol::allowContractPushers` with public oracle registration that later enables pool reads while multiple reports for the same feed arrive in different orders within one transaction or block, so that the attributed providers-oracle read path can be reached from a pool or provider context that should have been rejected along `public allowContractPushers -> staticcall isPusher(creator) -> namespaceRemapping update`, corrupting who is treated as an authorized contract pusher and which namespace their later fallback writes can mutate? This path trusts a live contract response instead of a signature, so any ambiguity in that trust boundary is publicly reachable. Trigger a public swap that arranges `inSwap()` and provider calls in a way the oracle misattributes.

Target
- File/function: smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol::allowContractPushers
- Entrypoint: smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol::allowContractPushers
- Attacker controls: public oracle registration that later enables pool reads
- Exploit idea: Reach `public allowContractPushers -> staticcall isPusher(creator) -> namespaceRemapping update` in a live public flow and show that trigger a public swap that arranges `inswap()` and provider calls in a way the oracle misattributes. The exact value at risk is who is treated as an authorized contract pusher and which namespace their later fallback writes can mutate.
- Invariant to test: Attributed oracle reads must be bound to the exact pool/provider pair that the live swap path intended to authorize. The concrete assertion should cover who is treated as an authorized contract pusher and which namespace their later fallback writes can mutate.
- Expected Immunefi impact: High if the wrong pool can consume a live quote from a feed it should not be allowed to read.
- Fast validation: Use varied contract-pusher behaviors and assert delegation only succeeds for the exact creator/contract relation the oracle intended to trust.
