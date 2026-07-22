Q20831: signature replay or domain-separation bug in compressed self-revocation and removal when a registration or blacklist side effect happened shortly before the next live provider read

Question
Can an unprivileged attacker enter through `smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol::{revokePusher,removePushers}` with public swaps that trigger the provider's attributed oracle read path while a registration or blacklist side effect happened shortly before the next live provider read, so that a valid signature for one context is also accepted in another context that changes who controls a namespace or feed update along `public revoke/remove -> namespaceRemapping clear -> later fallback pushes revert to creator or self namespace`, corrupting the namespace actually revoked, any surviving stale delegation, and whether later pushes still land in the old namespace? Delegation clean-up is a public surface because any stale remapping after revoke is effectively latent write authority. Reuse a public signature across creators, deadlines, contracts, or chain ids until the oracle accepts it outside its intended scope.

Target
- File/function: smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol::{revokePusher,removePushers}
- Entrypoint: smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol::{revokePusher,removePushers}
- Attacker controls: public swaps that trigger the provider's attributed oracle read path
- Exploit idea: Reach `public revoke/remove -> namespaceRemapping clear -> later fallback pushes revert to creator or self namespace` in a live public flow and show that reuse a public signature across creators, deadlines, contracts, or chain ids until the oracle accepts it outside its intended scope. The exact value at risk is the namespace actually revoked, any surviving stale delegation, and whether later pushes still land in the old namespace.
- Invariant to test: Every signed oracle action must be bound to one exact creator, chain, contract, and action payload. The concrete assertion should cover the namespace actually revoked, any surviving stale delegation, and whether later pushes still land in the old namespace.
- Expected Immunefi impact: Critical if a public attacker can hijack update authority over a production feed namespace.
- Fast validation: Exercise revoke/remove interleavings and assert no later public push can still write into a namespace that should have been detached.
