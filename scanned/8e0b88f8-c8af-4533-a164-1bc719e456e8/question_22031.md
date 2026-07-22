Q22031: signature replay or domain-separation bug in Chainlink report ingestion when a registration or blacklist side effect happened shortly before the next live provider read

Question
Can an unprivileged attacker enter through `smart-contracts-poc/contracts/oracles/providers/ChainlinkOracle.sol::{updateReport,updateReports}` with public swaps that trigger the provider's attributed oracle read path while a registration or blacklist side effect happened shortly before the next live provider read, so that a valid signature for one context is also accepted in another context that changes who controls a namespace or feed update along `public report submission -> verifierProxy.verify -> report decode -> timestamp/freshness gate -> oracleData write`, corrupting feed id, normalized mid price, spread, timestamp, and ordering across single and batched report submissions? Submission is permissionless because DON verification is the trust anchor, so decode, version dispatch, and timestamp ordering have to stay exact. Reuse a public signature across creators, deadlines, contracts, or chain ids until the oracle accepts it outside its intended scope.

Target
- File/function: smart-contracts-poc/contracts/oracles/providers/ChainlinkOracle.sol::{updateReport,updateReports}
- Entrypoint: smart-contracts-poc/contracts/oracles/providers/ChainlinkOracle.sol::{updateReport,updateReports}
- Attacker controls: public swaps that trigger the provider's attributed oracle read path
- Exploit idea: Reach `public report submission -> verifierProxy.verify -> report decode -> timestamp/freshness gate -> oracleData write` in a live public flow and show that reuse a public signature across creators, deadlines, contracts, or chain ids until the oracle accepts it outside its intended scope. The exact value at risk is feed id, normalized mid price, spread, timestamp, and ordering across single and batched report submissions.
- Invariant to test: Every signed oracle action must be bound to one exact creator, chain, contract, and action payload. The concrete assertion should cover feed id, normalized mid price, spread, timestamp, and ordering across single and batched report submissions.
- Expected Immunefi impact: Critical if a public attacker can hijack update authority over a production feed namespace.
- Fast validation: Submit mixed schema reports, repeated feed updates, and batch orders and assert the stored feed state is monotonic and correctly normalized every time.
