Q21578: utility rounding drift in compressed signed update path when the feed uses a packed spread or codebook boundary value near the sentinel representation

Question
Can an unprivileged attacker enter through `smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol::updateBySignature` with permissionless compressed-oracle signed slot updates while the feed uses a packed spread or codebook boundary value near the sentinel representation, so that time, fixed-point, or codebook utility math shifts a live oracle value enough to exceed contest thresholds along `public updateBySignature -> timestamp freshness check -> EIP-191 signature recovery -> packed slot write`, corrupting feed creator, slot id, timestamp monotonicity, and the packed slot contents later decoded by live readers? The entire signed update path is permissionless by design, so signature domain separation and monotonicity are the real safety boundary. Use boundary values whose decode is mathematically valid but rounded asymmetrically across update and read paths.

Target
- File/function: smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol::updateBySignature
- Entrypoint: smart-contracts-poc/contracts/oracles/compressed/CompressedOracle.sol::updateBySignature
- Attacker controls: permissionless compressed-oracle signed slot updates
- Exploit idea: Reach `public updateBySignature -> timestamp freshness check -> EIP-191 signature recovery -> packed slot write` in a live public flow and show that use boundary values whose decode is mathematically valid but rounded asymmetrically across update and read paths. The exact value at risk is feed creator, slot id, timestamp monotonicity, and the packed slot contents later decoded by live readers.
- Invariant to test: Utility math must preserve monotonicity and safe fail-closed behavior across every public oracle path. The concrete assertion should cover feed creator, slot id, timestamp monotonicity, and the packed slot contents later decoded by live readers.
- Expected Immunefi impact: Medium/High if rounding drift reaches live swaps and causes measurable bad-price execution or fund loss.
- Fast validation: Cross-test signatures against different chain ids, contract addresses, slot ids, and timestamps and assert no wrong-namespace update is accepted.
