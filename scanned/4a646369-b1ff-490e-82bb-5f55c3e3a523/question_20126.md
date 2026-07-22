Q20126: batch ordering anomaly in compressed pusher delegation when the target feed has a prior valid value and a new update sits on the timestamp boundary

Question
Can an unprivileged attacker enter through `smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol::allowPushers` with public oracle registration that later enables pool reads while the target feed has a prior valid value and a new update sits on the timestamp boundary, so that batched update helpers produce a different final feed state than equivalent single updates along `public allowPushers -> EIP-191 signature recovery -> namespaceRemapping update -> later fallback pushes use delegated namespace`, corrupting the delegated namespace owner, replay scope, and every future slot write attributed to the delegated pusher? Delegation is intentionally permissionless, so signature domain separation and replay resistance are the only things preventing namespace hijack. Submit the same logical update set in different public batch orders and look for a winner that should not have survived.

Target
- File/function: smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol::allowPushers
- Entrypoint: smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol::allowPushers
- Attacker controls: public oracle registration that later enables pool reads
- Exploit idea: Reach `public allowPushers -> EIP-191 signature recovery -> namespaceRemapping update -> later fallback pushes use delegated namespace` in a live public flow and show that submit the same logical update set in different public batch orders and look for a winner that should not have survived. The exact value at risk is the delegated namespace owner, replay scope, and every future slot write attributed to the delegated pusher.
- Invariant to test: Single and batched update surfaces must converge to the same canonical latest feed state. The concrete assertion should cover the delegated namespace owner, replay scope, and every future slot write attributed to the delegated pusher.
- Expected Immunefi impact: Medium/High if batch ordering lets a user keep a worse oracle state live.
- Fast validation: Replay and cross-context-test pusher signatures across creators, deadlines, chain ids, and contract addresses and assert no delegated namespace can be claimed twice.
