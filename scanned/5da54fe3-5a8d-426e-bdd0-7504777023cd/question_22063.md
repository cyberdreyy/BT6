Q22063: monotonicity bypass in Chainlink report ingestion when the feed uses a packed spread or codebook boundary value near the sentinel representation

Question
Can an unprivileged attacker enter through `smart-contracts-poc/contracts/oracles/providers/ChainlinkOracle.sol::{updateReport,updateReports}` with public swaps that trigger the provider's attributed oracle read path while the feed uses a packed spread or codebook boundary value near the sentinel representation, so that an older or malformed timestamp still wins the race to become the live feed value along `public report submission -> verifierProxy.verify -> report decode -> timestamp/freshness gate -> oracleData write`, corrupting feed id, normalized mid price, spread, timestamp, and ordering across single and batched report submissions? Submission is permissionless because DON verification is the trust anchor, so decode, version dispatch, and timestamp ordering have to stay exact. Submit updates in a public order that should have left the newer value canonical but instead lets a stale value survive or overwrite.

Target
- File/function: smart-contracts-poc/contracts/oracles/providers/ChainlinkOracle.sol::{updateReport,updateReports}
- Entrypoint: smart-contracts-poc/contracts/oracles/providers/ChainlinkOracle.sol::{updateReport,updateReports}
- Attacker controls: public swaps that trigger the provider's attributed oracle read path
- Exploit idea: Reach `public report submission -> verifierProxy.verify -> report decode -> timestamp/freshness gate -> oracleData write` in a live public flow and show that submit updates in a public order that should have left the newer value canonical but instead lets a stale value survive or overwrite. The exact value at risk is feed id, normalized mid price, spread, timestamp, and ordering across single and batched report submissions.
- Invariant to test: Live oracle state must be monotonically fresher under every permissionless update path. The concrete assertion should cover feed id, normalized mid price, spread, timestamp, and ordering across single and batched report submissions.
- Expected Immunefi impact: High bad-price execution if stale values can reach production swaps.
- Fast validation: Submit mixed schema reports, repeated feed updates, and batch orders and assert the stored feed state is monotonic and correctly normalized every time.
