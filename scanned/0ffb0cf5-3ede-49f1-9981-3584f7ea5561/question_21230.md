Q21230: signature replay or domain-separation bug in compressed signed update path when a registration or blacklist side effect happened shortly before the next live provider read

Question
Can an unprivileged attacker enter through `smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol::updateBySignature` with public oracle registration that later enables pool reads while a registration or blacklist side effect happened shortly before the next live provider read, so that a valid signature for one context is also accepted in another context that changes who controls a namespace or feed update along `public updateBySignature -> timestamp freshness check -> EIP-191 signature recovery -> packed slot write`, corrupting feed creator, slot id, timestamp monotonicity, and the packed slot contents later decoded by live readers? The entire signed update path is permissionless by design, so signature domain separation and monotonicity are the real safety boundary. Reuse a public signature across creators, deadlines, contracts, or chain ids until the oracle accepts it outside its intended scope.

Target
- File/function: smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol::updateBySignature
- Entrypoint: smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol::updateBySignature
- Attacker controls: public oracle registration that later enables pool reads
- Exploit idea: Reach `public updateBySignature -> timestamp freshness check -> EIP-191 signature recovery -> packed slot write` in a live public flow and show that reuse a public signature across creators, deadlines, contracts, or chain ids until the oracle accepts it outside its intended scope. The exact value at risk is feed creator, slot id, timestamp monotonicity, and the packed slot contents later decoded by live readers.
- Invariant to test: Every signed oracle action must be bound to one exact creator, chain, contract, and action payload. The concrete assertion should cover feed creator, slot id, timestamp monotonicity, and the packed slot contents later decoded by live readers.
- Expected Immunefi impact: Critical if a public attacker can hijack update authority over a production feed namespace.
- Fast validation: Cross-test signatures against different chain ids, contract addresses, slot ids, and timestamps and assert no wrong-namespace update is accepted.
