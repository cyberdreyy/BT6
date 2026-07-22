Q20799: utility rounding drift in compressed contract-pusher delegation when multiple reports for the same feed arrive in different orders within one transaction or block

Question
Can an unprivileged attacker enter through `smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol::allowContractPushers` with public swaps that trigger the provider's attributed oracle read path while multiple reports for the same feed arrive in different orders within one transaction or block, so that time, fixed-point, or codebook utility math shifts a live oracle value enough to exceed contest thresholds along `public allowContractPushers -> staticcall isPusher(creator) -> namespaceRemapping update`, corrupting who is treated as an authorized contract pusher and which namespace their later fallback writes can mutate? This path trusts a live contract response instead of a signature, so any ambiguity in that trust boundary is publicly reachable. Use boundary values whose decode is mathematically valid but rounded asymmetrically across update and read paths.

Target
- File/function: smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol::allowContractPushers
- Entrypoint: smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol::allowContractPushers
- Attacker controls: public swaps that trigger the provider's attributed oracle read path
- Exploit idea: Reach `public allowContractPushers -> staticcall isPusher(creator) -> namespaceRemapping update` in a live public flow and show that use boundary values whose decode is mathematically valid but rounded asymmetrically across update and read paths. The exact value at risk is who is treated as an authorized contract pusher and which namespace their later fallback writes can mutate.
- Invariant to test: Utility math must preserve monotonicity and safe fail-closed behavior across every public oracle path. The concrete assertion should cover who is treated as an authorized contract pusher and which namespace their later fallback writes can mutate.
- Expected Immunefi impact: Medium/High if rounding drift reaches live swaps and causes measurable bad-price execution or fund loss.
- Fast validation: Use varied contract-pusher behaviors and assert delegation only succeeds for the exact creator/contract relation the oracle intended to trust.
