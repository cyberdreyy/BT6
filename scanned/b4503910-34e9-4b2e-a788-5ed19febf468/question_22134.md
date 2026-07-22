Q22134: batch ordering anomaly in Chainlink report ingestion when the target feed has never been pushed before and its zero-value sentinel behavior matters

Question
Can an unprivileged attacker enter through `smart-contracts-poc/contracts/oracles/providers/ChainlinkOracle.sol::{updateReport,updateReports}` with public oracle registration that later enables pool reads while the target feed has never been pushed before and its zero-value sentinel behavior matters, so that batched update helpers produce a different final feed state than equivalent single updates along `public report submission -> verifierProxy.verify -> report decode -> timestamp/freshness gate -> oracleData write`, corrupting feed id, normalized mid price, spread, timestamp, and ordering across single and batched report submissions? Submission is permissionless because DON verification is the trust anchor, so decode, version dispatch, and timestamp ordering have to stay exact. Submit the same logical update set in different public batch orders and look for a winner that should not have survived.

Target
- File/function: smart-contracts-poc/contracts/oracles/providers/ChainlinkOracle.sol::{updateReport,updateReports}
- Entrypoint: smart-contracts-poc/contracts/oracles/providers/ChainlinkOracle.sol::{updateReport,updateReports}
- Attacker controls: public oracle registration that later enables pool reads
- Exploit idea: Reach `public report submission -> verifierProxy.verify -> report decode -> timestamp/freshness gate -> oracleData write` in a live public flow and show that submit the same logical update set in different public batch orders and look for a winner that should not have survived. The exact value at risk is feed id, normalized mid price, spread, timestamp, and ordering across single and batched report submissions.
- Invariant to test: Single and batched update surfaces must converge to the same canonical latest feed state. The concrete assertion should cover feed id, normalized mid price, spread, timestamp, and ordering across single and batched report submissions.
- Expected Immunefi impact: Medium/High if batch ordering lets a user keep a worse oracle state live.
- Fast validation: Submit mixed schema reports, repeated feed updates, and batch orders and assert the stored feed state is monotonic and correctly normalized every time.
