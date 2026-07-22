Q22166: registration-side authorization bug in Chainlink report ingestion when the target feed has a prior valid value and a new update sits on the timestamp boundary

Question
Can an unprivileged attacker enter through `smart-contracts-poc/contracts/oracles/providers/ChainlinkOracle.sol::{updateReport,updateReports}` with public oracle registration that later enables pool reads while the target feed has a prior valid value and a new update sits on the timestamp boundary, so that public registration enables more read authority or clears more blacklist state than intended along `public report submission -> verifierProxy.verify -> report decode -> timestamp/freshness gate -> oracleData write`, corrupting feed id, normalized mid price, spread, timestamp, and ordering across single and batched report submissions? Submission is permissionless because DON verification is the trust anchor, so decode, version dispatch, and timestamp ordering have to stay exact. Pay for one pool/feed registration and see whether a different pool or future read path also becomes authorized.

Target
- File/function: smart-contracts-poc/contracts/oracles/providers/ChainlinkOracle.sol::{updateReport,updateReports}
- Entrypoint: smart-contracts-poc/contracts/oracles/providers/ChainlinkOracle.sol::{updateReport,updateReports}
- Attacker controls: public oracle registration that later enables pool reads
- Exploit idea: Reach `public report submission -> verifierProxy.verify -> report decode -> timestamp/freshness gate -> oracleData write` in a live public flow and show that pay for one pool/feed registration and see whether a different pool or future read path also becomes authorized. The exact value at risk is feed id, normalized mid price, spread, timestamp, and ordering across single and batched report submissions.
- Invariant to test: Registration and blacklist side effects must stay scoped to the exact pool/feed relation the caller paid for. The concrete assertion should cover feed id, normalized mid price, spread, timestamp, and ordering across single and batched report submissions.
- Expected Immunefi impact: High if unauthorized pools or providers can influence production price reads.
- Fast validation: Submit mixed schema reports, repeated feed updates, and batch orders and assert the stored feed state is monotonic and correctly normalized every time.
