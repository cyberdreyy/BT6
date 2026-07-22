Q20973: registration-side authorization bug in compressed self-revocation and removal when the target feed has never been pushed before and its zero-value sentinel behavior matters

Question
Can an unprivileged attacker enter through `smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol::{revokePusher,removePushers}` with permissionless Pyth Lazer fallback payload submission while the target feed has never been pushed before and its zero-value sentinel behavior matters, so that public registration enables more read authority or clears more blacklist state than intended along `public revoke/remove -> namespaceRemapping clear -> later fallback pushes revert to creator or self namespace`, corrupting the namespace actually revoked, any surviving stale delegation, and whether later pushes still land in the old namespace? Delegation clean-up is a public surface because any stale remapping after revoke is effectively latent write authority. Pay for one pool/feed registration and see whether a different pool or future read path also becomes authorized.

Target
- File/function: smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol::{revokePusher,removePushers}
- Entrypoint: smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol::{revokePusher,removePushers}
- Attacker controls: permissionless Pyth Lazer fallback payload submission
- Exploit idea: Reach `public revoke/remove -> namespaceRemapping clear -> later fallback pushes revert to creator or self namespace` in a live public flow and show that pay for one pool/feed registration and see whether a different pool or future read path also becomes authorized. The exact value at risk is the namespace actually revoked, any surviving stale delegation, and whether later pushes still land in the old namespace.
- Invariant to test: Registration and blacklist side effects must stay scoped to the exact pool/feed relation the caller paid for. The concrete assertion should cover the namespace actually revoked, any surviving stale delegation, and whether later pushes still land in the old namespace.
- Expected Immunefi impact: High if unauthorized pools or providers can influence production price reads.
- Fast validation: Exercise revoke/remove interleavings and assert no later public push can still write into a namespace that should have been detached.
