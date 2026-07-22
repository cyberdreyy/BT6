Q20024: signature replay or domain-separation bug in compressed pusher delegation when the feed uses a packed spread or codebook boundary value near the sentinel representation

Question
Can an unprivileged attacker enter through `smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol::allowPushers` with batched or repeated updates where newer and older reports race in the same block while the feed uses a packed spread or codebook boundary value near the sentinel representation, so that a valid signature for one context is also accepted in another context that changes who controls a namespace or feed update along `public allowPushers -> EIP-191 signature recovery -> namespaceRemapping update -> later fallback pushes use delegated namespace`, corrupting the delegated namespace owner, replay scope, and every future slot write attributed to the delegated pusher? Delegation is intentionally permissionless, so signature domain separation and replay resistance are the only things preventing namespace hijack. Reuse a public signature across creators, deadlines, contracts, or chain ids until the oracle accepts it outside its intended scope.

Target
- File/function: smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol::allowPushers
- Entrypoint: smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol::allowPushers
- Attacker controls: batched or repeated updates where newer and older reports race in the same block
- Exploit idea: Reach `public allowPushers -> EIP-191 signature recovery -> namespaceRemapping update -> later fallback pushes use delegated namespace` in a live public flow and show that reuse a public signature across creators, deadlines, contracts, or chain ids until the oracle accepts it outside its intended scope. The exact value at risk is the delegated namespace owner, replay scope, and every future slot write attributed to the delegated pusher.
- Invariant to test: Every signed oracle action must be bound to one exact creator, chain, contract, and action payload. The concrete assertion should cover the delegated namespace owner, replay scope, and every future slot write attributed to the delegated pusher.
- Expected Immunefi impact: Critical if a public attacker can hijack update authority over a production feed namespace.
- Fast validation: Replay and cross-context-test pusher signatures across creators, deadlines, chain ids, and contract addresses and assert no delegated namespace can be claimed twice.
