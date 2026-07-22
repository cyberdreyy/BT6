Q21368: registration-side authorization bug in compressed signed update path when the target feed has a prior valid value and a new update sits on the timestamp boundary

Question
Can an unprivileged attacker enter through `smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol::updateBySignature` with batched or repeated updates where newer and older reports race in the same block while the target feed has a prior valid value and a new update sits on the timestamp boundary, so that public registration enables more read authority or clears more blacklist state than intended along `public updateBySignature -> timestamp freshness check -> EIP-191 signature recovery -> packed slot write`, corrupting feed creator, slot id, timestamp monotonicity, and the packed slot contents later decoded by live readers? The entire signed update path is permissionless by design, so signature domain separation and monotonicity are the real safety boundary. Pay for one pool/feed registration and see whether a different pool or future read path also becomes authorized.

Target
- File/function: smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol::updateBySignature
- Entrypoint: smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol::updateBySignature
- Attacker controls: batched or repeated updates where newer and older reports race in the same block
- Exploit idea: Reach `public updateBySignature -> timestamp freshness check -> EIP-191 signature recovery -> packed slot write` in a live public flow and show that pay for one pool/feed registration and see whether a different pool or future read path also becomes authorized. The exact value at risk is feed creator, slot id, timestamp monotonicity, and the packed slot contents later decoded by live readers.
- Invariant to test: Registration and blacklist side effects must stay scoped to the exact pool/feed relation the caller paid for. The concrete assertion should cover feed creator, slot id, timestamp monotonicity, and the packed slot contents later decoded by live readers.
- Expected Immunefi impact: High if unauthorized pools or providers can influence production price reads.
- Fast validation: Cross-test signatures against different chain ids, contract addresses, slot ids, and timestamps and assert no wrong-namespace update is accepted.
