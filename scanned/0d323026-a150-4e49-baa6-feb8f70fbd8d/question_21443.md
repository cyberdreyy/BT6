Q21443: schema or resolution mix-up in compressed signed update path when the target feed has a prior valid value and a new update sits on the timestamp boundary

Question
Can an unprivileged attacker enter through `smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol::updateBySignature` with permissionless compressed-oracle batch fallback pushes while the target feed has a prior valid value and a new update sits on the timestamp boundary, so that report version or timestamp-resolution dispatch decodes valid signed data under the wrong schema family along `public updateBySignature -> timestamp freshness check -> EIP-191 signature recovery -> packed slot write`, corrupting feed creator, slot id, timestamp monotonicity, and the packed slot contents later decoded by live readers? The entire signed update path is permissionless by design, so signature domain separation and monotonicity are the real safety boundary. Submit a real report whose feed id sits near a schema or resolution boundary and see whether decode logic takes the wrong branch.

Target
- File/function: smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol::updateBySignature
- Entrypoint: smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol::updateBySignature
- Attacker controls: permissionless compressed-oracle batch fallback pushes
- Exploit idea: Reach `public updateBySignature -> timestamp freshness check -> EIP-191 signature recovery -> packed slot write` in a live public flow and show that submit a real report whose feed id sits near a schema or resolution boundary and see whether decode logic takes the wrong branch. The exact value at risk is feed creator, slot id, timestamp monotonicity, and the packed slot contents later decoded by live readers.
- Invariant to test: Every verified report must be decoded by exactly the schema and time-resolution family it was signed for. The concrete assertion should cover feed creator, slot id, timestamp monotonicity, and the packed slot contents later decoded by live readers.
- Expected Immunefi impact: High bad-price execution if normalized oracle data is wrong despite successful verification.
- Fast validation: Cross-test signatures against different chain ids, contract addresses, slot ids, and timestamps and assert no wrong-namespace update is accepted.
