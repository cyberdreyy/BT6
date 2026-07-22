Q20501: packed-slot decode confusion in compressed contract-pusher delegation when the feed uses a packed spread or codebook boundary value near the sentinel representation

Question
Can an unprivileged attacker enter through `smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol::allowContractPushers` with permissionless Pyth Lazer fallback payload submission while the feed uses a packed spread or codebook boundary value near the sentinel representation, so that slot packing, sentinel markers, or codebook boundaries decode into a valid-looking price or spread that should have failed closed along `public allowContractPushers -> staticcall isPusher(creator) -> namespaceRemapping update`, corrupting who is treated as an authorized contract pusher and which namespace their later fallback writes can mutate? This path trusts a live contract response instead of a signature, so any ambiguity in that trust boundary is publicly reachable. Push or read a boundary compressed value whose spread or timestamp representation is easy to misinterpret.

Target
- File/function: smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol::allowContractPushers
- Entrypoint: smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol::allowContractPushers
- Attacker controls: permissionless Pyth Lazer fallback payload submission
- Exploit idea: Reach `public allowContractPushers -> staticcall isPusher(creator) -> namespaceRemapping update` in a live public flow and show that push or read a boundary compressed value whose spread or timestamp representation is easy to misinterpret. The exact value at risk is who is treated as an authorized contract pusher and which namespace their later fallback writes can mutate.
- Invariant to test: Packed compressed data must decode unambiguously and reject every sentinel or malformed boundary state before price consumers trust it. The concrete assertion should cover who is treated as an authorized contract pusher and which namespace their later fallback writes can mutate.
- Expected Immunefi impact: Critical if a malformed compressed value can drive live pool pricing.
- Fast validation: Use varied contract-pusher behaviors and assert delegation only succeeds for the exact creator/contract relation the oracle intended to trust.
