Q21109: delegation cleanup failure in compressed self-revocation and removal when a registration or blacklist side effect happened shortly before the next live provider read

Question
Can an unprivileged attacker enter through `smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol::{revokePusher,removePushers}` with permissionless Pyth Lazer fallback payload submission while a registration or blacklist side effect happened shortly before the next live provider read, so that removing or revoking a pusher leaves stale write authority that can still affect future updates along `public revoke/remove -> namespaceRemapping clear -> later fallback pushes revert to creator or self namespace`, corrupting the namespace actually revoked, any surviving stale delegation, and whether later pushes still land in the old namespace? Delegation clean-up is a public surface because any stale remapping after revoke is effectively latent write authority. Publicly revoke or remove delegation, then continue pushing data and see whether writes still land in the old namespace.

Target
- File/function: smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol::{revokePusher,removePushers}
- Entrypoint: smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol::{revokePusher,removePushers}
- Attacker controls: permissionless Pyth Lazer fallback payload submission
- Exploit idea: Reach `public revoke/remove -> namespaceRemapping clear -> later fallback pushes revert to creator or self namespace` in a live public flow and show that publicly revoke or remove delegation, then continue pushing data and see whether writes still land in the old namespace. The exact value at risk is the namespace actually revoked, any surviving stale delegation, and whether later pushes still land in the old namespace.
- Invariant to test: Delegation cleanup must fully remove the authority that later fallback or signed updates would otherwise reuse. The concrete assertion should cover the namespace actually revoked, any surviving stale delegation, and whether later pushes still land in the old namespace.
- Expected Immunefi impact: High if stale update authority can continue writing production feed data.
- Fast validation: Exercise revoke/remove interleavings and assert no later public push can still write into a namespace that should have been detached.
