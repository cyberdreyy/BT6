Q22268: schema or resolution mix-up in Chainlink report ingestion when a registration or blacklist side effect happened shortly before the next live provider read

Question
Can an unprivileged attacker enter through `smart-contracts-poc/contracts/oracles/providers/ChainlinkOracle.sol::{updateReport,updateReports}` with permissionless Chainlink Data Streams report submission while a registration or blacklist side effect happened shortly before the next live provider read, so that report version or timestamp-resolution dispatch decodes valid signed data under the wrong schema family along `public report submission -> verifierProxy.verify -> report decode -> timestamp/freshness gate -> oracleData write`, corrupting feed id, normalized mid price, spread, timestamp, and ordering across single and batched report submissions? Submission is permissionless because DON verification is the trust anchor, so decode, version dispatch, and timestamp ordering have to stay exact. Submit a real report whose feed id sits near a schema or resolution boundary and see whether decode logic takes the wrong branch.

Target
- File/function: smart-contracts-poc/contracts/oracles/providers/ChainlinkOracle.sol::{updateReport,updateReports}
- Entrypoint: smart-contracts-poc/contracts/oracles/providers/ChainlinkOracle.sol::{updateReport,updateReports}
- Attacker controls: permissionless Chainlink Data Streams report submission
- Exploit idea: Reach `public report submission -> verifierProxy.verify -> report decode -> timestamp/freshness gate -> oracleData write` in a live public flow and show that submit a real report whose feed id sits near a schema or resolution boundary and see whether decode logic takes the wrong branch. The exact value at risk is feed id, normalized mid price, spread, timestamp, and ordering across single and batched report submissions.
- Invariant to test: Every verified report must be decoded by exactly the schema and time-resolution family it was signed for. The concrete assertion should cover feed id, normalized mid price, spread, timestamp, and ordering across single and batched report submissions.
- Expected Immunefi impact: High bad-price execution if normalized oracle data is wrong despite successful verification.
- Fast validation: Submit mixed schema reports, repeated feed updates, and batch orders and assert the stored feed state is monotonic and correctly normalized every time.
