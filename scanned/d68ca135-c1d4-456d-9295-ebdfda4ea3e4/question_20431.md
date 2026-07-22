Q20431: signature replay or domain-separation bug in compressed contract-pusher delegation when a registration or blacklist side effect happened shortly before the next live provider read

Question
Can an unprivileged attacker enter through `smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol::allowContractPushers` with public swaps that trigger the provider's attributed oracle read path while a registration or blacklist side effect happened shortly before the next live provider read, so that a valid signature for one context is also accepted in another context that changes who controls a namespace or feed update along `public allowContractPushers -> staticcall isPusher(creator) -> namespaceRemapping update`, corrupting who is treated as an authorized contract pusher and which namespace their later fallback writes can mutate? This path trusts a live contract response instead of a signature, so any ambiguity in that trust boundary is publicly reachable. Reuse a public signature across creators, deadlines, contracts, or chain ids until the oracle accepts it outside its intended scope.

Target
- File/function: smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol::allowContractPushers
- Entrypoint: smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol::allowContractPushers
- Attacker controls: public swaps that trigger the provider's attributed oracle read path
- Exploit idea: Reach `public allowContractPushers -> staticcall isPusher(creator) -> namespaceRemapping update` in a live public flow and show that reuse a public signature across creators, deadlines, contracts, or chain ids until the oracle accepts it outside its intended scope. The exact value at risk is who is treated as an authorized contract pusher and which namespace their later fallback writes can mutate.
- Invariant to test: Every signed oracle action must be bound to one exact creator, chain, contract, and action payload. The concrete assertion should cover who is treated as an authorized contract pusher and which namespace their later fallback writes can mutate.
- Expected Immunefi impact: Critical if a public attacker can hijack update authority over a production feed namespace.
- Fast validation: Use varied contract-pusher behaviors and assert delegation only succeeds for the exact creator/contract relation the oracle intended to trust.
