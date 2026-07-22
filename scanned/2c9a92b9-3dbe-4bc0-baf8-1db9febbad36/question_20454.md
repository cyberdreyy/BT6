Q20454: monotonicity bypass in compressed contract-pusher delegation when the target feed has never been pushed before and its zero-value sentinel behavior matters

Question
Can an unprivileged attacker enter through `smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol::allowContractPushers` with public oracle registration that later enables pool reads while the target feed has never been pushed before and its zero-value sentinel behavior matters, so that an older or malformed timestamp still wins the race to become the live feed value along `public allowContractPushers -> staticcall isPusher(creator) -> namespaceRemapping update`, corrupting who is treated as an authorized contract pusher and which namespace their later fallback writes can mutate? This path trusts a live contract response instead of a signature, so any ambiguity in that trust boundary is publicly reachable. Submit updates in a public order that should have left the newer value canonical but instead lets a stale value survive or overwrite.

Target
- File/function: smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol::allowContractPushers
- Entrypoint: smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol::allowContractPushers
- Attacker controls: public oracle registration that later enables pool reads
- Exploit idea: Reach `public allowContractPushers -> staticcall isPusher(creator) -> namespaceRemapping update` in a live public flow and show that submit updates in a public order that should have left the newer value canonical but instead lets a stale value survive or overwrite. The exact value at risk is who is treated as an authorized contract pusher and which namespace their later fallback writes can mutate.
- Invariant to test: Live oracle state must be monotonically fresher under every permissionless update path. The concrete assertion should cover who is treated as an authorized contract pusher and which namespace their later fallback writes can mutate.
- Expected Immunefi impact: High bad-price execution if stale values can reach production swaps.
- Fast validation: Use varied contract-pusher behaviors and assert delegation only succeeds for the exact creator/contract relation the oracle intended to trust.
