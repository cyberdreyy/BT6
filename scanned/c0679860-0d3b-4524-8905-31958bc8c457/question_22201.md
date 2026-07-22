Q22201: attribution bypass in Chainlink report ingestion when the target feed has a prior valid value and a new update sits on the timestamp boundary

Question
Can an unprivileged attacker enter through `smart-contracts-poc/contracts/oracles/providers/ChainlinkOracle.sol::{updateReport,updateReports}` with permissionless compressed-oracle pusher delegation and revocation calls while the target feed has a prior valid value and a new update sits on the timestamp boundary, so that the attributed providers-oracle read path can be reached from a pool or provider context that should have been rejected along `public report submission -> verifierProxy.verify -> report decode -> timestamp/freshness gate -> oracleData write`, corrupting feed id, normalized mid price, spread, timestamp, and ordering across single and batched report submissions? Submission is permissionless because DON verification is the trust anchor, so decode, version dispatch, and timestamp ordering have to stay exact. Trigger a public swap that arranges `inSwap()` and provider calls in a way the oracle misattributes.

Target
- File/function: smart-contracts-poc/contracts/oracles/providers/ChainlinkOracle.sol::{updateReport,updateReports}
- Entrypoint: smart-contracts-poc/contracts/oracles/providers/ChainlinkOracle.sol::{updateReport,updateReports}
- Attacker controls: permissionless compressed-oracle pusher delegation and revocation calls
- Exploit idea: Reach `public report submission -> verifierProxy.verify -> report decode -> timestamp/freshness gate -> oracleData write` in a live public flow and show that trigger a public swap that arranges `inswap()` and provider calls in a way the oracle misattributes. The exact value at risk is feed id, normalized mid price, spread, timestamp, and ordering across single and batched report submissions.
- Invariant to test: Attributed oracle reads must be bound to the exact pool/provider pair that the live swap path intended to authorize. The concrete assertion should cover feed id, normalized mid price, spread, timestamp, and ordering across single and batched report submissions.
- Expected Immunefi impact: High if the wrong pool can consume a live quote from a feed it should not be allowed to read.
- Fast validation: Submit mixed schema reports, repeated feed updates, and batch orders and assert the stored feed state is monotonic and correctly normalized every time.
