Q21017: attribution bypass in compressed self-revocation and removal when the feed uses a packed spread or codebook boundary value near the sentinel representation

Question
Can an unprivileged attacker enter through `smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol::{revokePusher,removePushers}` with permissionless compressed-oracle pusher delegation and revocation calls while the feed uses a packed spread or codebook boundary value near the sentinel representation, so that the attributed providers-oracle read path can be reached from a pool or provider context that should have been rejected along `public revoke/remove -> namespaceRemapping clear -> later fallback pushes revert to creator or self namespace`, corrupting the namespace actually revoked, any surviving stale delegation, and whether later pushes still land in the old namespace? Delegation clean-up is a public surface because any stale remapping after revoke is effectively latent write authority. Trigger a public swap that arranges `inSwap()` and provider calls in a way the oracle misattributes.

Target
- File/function: smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol::{revokePusher,removePushers}
- Entrypoint: smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol::{revokePusher,removePushers}
- Attacker controls: permissionless compressed-oracle pusher delegation and revocation calls
- Exploit idea: Reach `public revoke/remove -> namespaceRemapping clear -> later fallback pushes revert to creator or self namespace` in a live public flow and show that trigger a public swap that arranges `inswap()` and provider calls in a way the oracle misattributes. The exact value at risk is the namespace actually revoked, any surviving stale delegation, and whether later pushes still land in the old namespace.
- Invariant to test: Attributed oracle reads must be bound to the exact pool/provider pair that the live swap path intended to authorize. The concrete assertion should cover the namespace actually revoked, any surviving stale delegation, and whether later pushes still land in the old namespace.
- Expected Immunefi impact: High if the wrong pool can consume a live quote from a feed it should not be allowed to read.
- Fast validation: Exercise revoke/remove interleavings and assert no later public push can still write into a namespace that should have been detached.
