Q20062: monotonicity bypass in compressed pusher delegation when the feed uses a packed spread or codebook boundary value near the sentinel representation

Question
Can an unprivileged attacker enter through `smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol::allowPushers` with public oracle registration that later enables pool reads while the feed uses a packed spread or codebook boundary value near the sentinel representation, so that an older or malformed timestamp still wins the race to become the live feed value along `public allowPushers -> EIP-191 signature recovery -> namespaceRemapping update -> later fallback pushes use delegated namespace`, corrupting the delegated namespace owner, replay scope, and every future slot write attributed to the delegated pusher? Delegation is intentionally permissionless, so signature domain separation and replay resistance are the only things preventing namespace hijack. Submit updates in a public order that should have left the newer value canonical but instead lets a stale value survive or overwrite.

Target
- File/function: smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol::allowPushers
- Entrypoint: smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol::allowPushers
- Attacker controls: public oracle registration that later enables pool reads
- Exploit idea: Reach `public allowPushers -> EIP-191 signature recovery -> namespaceRemapping update -> later fallback pushes use delegated namespace` in a live public flow and show that submit updates in a public order that should have left the newer value canonical but instead lets a stale value survive or overwrite. The exact value at risk is the delegated namespace owner, replay scope, and every future slot write attributed to the delegated pusher.
- Invariant to test: Live oracle state must be monotonically fresher under every permissionless update path. The concrete assertion should cover the delegated namespace owner, replay scope, and every future slot write attributed to the delegated pusher.
- Expected Immunefi impact: High bad-price execution if stale values can reach production swaps.
- Fast validation: Replay and cross-context-test pusher signatures across creators, deadlines, chain ids, and contract addresses and assert no delegated namespace can be claimed twice.
