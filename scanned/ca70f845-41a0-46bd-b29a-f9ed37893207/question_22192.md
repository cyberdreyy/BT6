Q22192: registration-side authorization bug in Chainlink report ingestion when a registration or blacklist side effect happened shortly before the next live provider read

Question
Can an unprivileged attacker enter through `smart-contracts-poc/contracts/oracles/providers/ChainlinkOracle.sol::{updateReport,updateReports}` with batched or repeated updates where newer and older reports race in the same block while a registration or blacklist side effect happened shortly before the next live provider read, so that public registration enables more read authority or clears more blacklist state than intended along `public report submission -> verifierProxy.verify -> report decode -> timestamp/freshness gate -> oracleData write`, corrupting feed id, normalized mid price, spread, timestamp, and ordering across single and batched report submissions? Submission is permissionless because DON verification is the trust anchor, so decode, version dispatch, and timestamp ordering have to stay exact. Pay for one pool/feed registration and see whether a different pool or future read path also becomes authorized.

Target
- File/function: smart-contracts-poc/contracts/oracles/providers/ChainlinkOracle.sol::{updateReport,updateReports}
- Entrypoint: smart-contracts-poc/contracts/oracles/providers/ChainlinkOracle.sol::{updateReport,updateReports}
- Attacker controls: batched or repeated updates where newer and older reports race in the same block
- Exploit idea: Reach `public report submission -> verifierProxy.verify -> report decode -> timestamp/freshness gate -> oracleData write` in a live public flow and show that pay for one pool/feed registration and see whether a different pool or future read path also becomes authorized. The exact value at risk is feed id, normalized mid price, spread, timestamp, and ordering across single and batched report submissions.
- Invariant to test: Registration and blacklist side effects must stay scoped to the exact pool/feed relation the caller paid for. The concrete assertion should cover feed id, normalized mid price, spread, timestamp, and ordering across single and batched report submissions.
- Expected Immunefi impact: High if unauthorized pools or providers can influence production price reads.
- Fast validation: Submit mixed schema reports, repeated feed updates, and batch orders and assert the stored feed state is monotonic and correctly normalized every time.
