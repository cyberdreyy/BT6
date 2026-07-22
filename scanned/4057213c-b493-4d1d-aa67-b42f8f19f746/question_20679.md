Q20679: schema or resolution mix-up in compressed contract-pusher delegation when multiple reports for the same feed arrive in different orders within one transaction or block

Question
Can an unprivileged attacker enter through `smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol::allowContractPushers` with public swaps that trigger the provider's attributed oracle read path while multiple reports for the same feed arrive in different orders within one transaction or block, so that report version or timestamp-resolution dispatch decodes valid signed data under the wrong schema family along `public allowContractPushers -> staticcall isPusher(creator) -> namespaceRemapping update`, corrupting who is treated as an authorized contract pusher and which namespace their later fallback writes can mutate? This path trusts a live contract response instead of a signature, so any ambiguity in that trust boundary is publicly reachable. Submit a real report whose feed id sits near a schema or resolution boundary and see whether decode logic takes the wrong branch.

Target
- File/function: smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol::allowContractPushers
- Entrypoint: smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol::allowContractPushers
- Attacker controls: public swaps that trigger the provider's attributed oracle read path
- Exploit idea: Reach `public allowContractPushers -> staticcall isPusher(creator) -> namespaceRemapping update` in a live public flow and show that submit a real report whose feed id sits near a schema or resolution boundary and see whether decode logic takes the wrong branch. The exact value at risk is who is treated as an authorized contract pusher and which namespace their later fallback writes can mutate.
- Invariant to test: Every verified report must be decoded by exactly the schema and time-resolution family it was signed for. The concrete assertion should cover who is treated as an authorized contract pusher and which namespace their later fallback writes can mutate.
- Expected Immunefi impact: High bad-price execution if normalized oracle data is wrong despite successful verification.
- Fast validation: Use varied contract-pusher behaviors and assert delegation only succeeds for the exact creator/contract relation the oracle intended to trust.
