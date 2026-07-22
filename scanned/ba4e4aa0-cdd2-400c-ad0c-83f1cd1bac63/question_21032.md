Q21032: attribution bypass in compressed self-revocation and removal when a registration or blacklist side effect happened shortly before the next live provider read

Question
Can an unprivileged attacker enter through `smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol::{revokePusher,removePushers}` with batched or repeated updates where newer and older reports race in the same block while a registration or blacklist side effect happened shortly before the next live provider read, so that the attributed providers-oracle read path can be reached from a pool or provider context that should have been rejected along `public revoke/remove -> namespaceRemapping clear -> later fallback pushes revert to creator or self namespace`, corrupting the namespace actually revoked, any surviving stale delegation, and whether later pushes still land in the old namespace? Delegation clean-up is a public surface because any stale remapping after revoke is effectively latent write authority. Trigger a public swap that arranges `inSwap()` and provider calls in a way the oracle misattributes.

Target
- File/function: smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol::{revokePusher,removePushers}
- Entrypoint: smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol::{revokePusher,removePushers}
- Attacker controls: batched or repeated updates where newer and older reports race in the same block
- Exploit idea: Reach `public revoke/remove -> namespaceRemapping clear -> later fallback pushes revert to creator or self namespace` in a live public flow and show that trigger a public swap that arranges `inswap()` and provider calls in a way the oracle misattributes. The exact value at risk is the namespace actually revoked, any surviving stale delegation, and whether later pushes still land in the old namespace.
- Invariant to test: Attributed oracle reads must be bound to the exact pool/provider pair that the live swap path intended to authorize. The concrete assertion should cover the namespace actually revoked, any surviving stale delegation, and whether later pushes still land in the old namespace.
- Expected Immunefi impact: High if the wrong pool can consume a live quote from a feed it should not be allowed to read.
- Fast validation: Exercise revoke/remove interleavings and assert no later public push can still write into a namespace that should have been detached.
