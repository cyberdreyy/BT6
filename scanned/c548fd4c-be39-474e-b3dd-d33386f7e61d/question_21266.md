Q21266: monotonicity bypass in compressed signed update path when a registration or blacklist side effect happened shortly before the next live provider read

Question
Can an unprivileged attacker enter through `smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol::updateBySignature` with permissionless compressed-oracle signed slot updates while a registration or blacklist side effect happened shortly before the next live provider read, so that an older or malformed timestamp still wins the race to become the live feed value along `public updateBySignature -> timestamp freshness check -> EIP-191 signature recovery -> packed slot write`, corrupting feed creator, slot id, timestamp monotonicity, and the packed slot contents later decoded by live readers? The entire signed update path is permissionless by design, so signature domain separation and monotonicity are the real safety boundary. Submit updates in a public order that should have left the newer value canonical but instead lets a stale value survive or overwrite.

Target
- File/function: smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol::updateBySignature
- Entrypoint: smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol::updateBySignature
- Attacker controls: permissionless compressed-oracle signed slot updates
- Exploit idea: Reach `public updateBySignature -> timestamp freshness check -> EIP-191 signature recovery -> packed slot write` in a live public flow and show that submit updates in a public order that should have left the newer value canonical but instead lets a stale value survive or overwrite. The exact value at risk is feed creator, slot id, timestamp monotonicity, and the packed slot contents later decoded by live readers.
- Invariant to test: Live oracle state must be monotonically fresher under every permissionless update path. The concrete assertion should cover feed creator, slot id, timestamp monotonicity, and the packed slot contents later decoded by live readers.
- Expected Immunefi impact: High bad-price execution if stale values can reach production swaps.
- Fast validation: Cross-test signatures against different chain ids, contract addresses, slot ids, and timestamps and assert no wrong-namespace update is accepted.
