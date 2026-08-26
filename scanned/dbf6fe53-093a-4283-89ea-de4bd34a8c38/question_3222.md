# Q3222: quorum/threshold check weak in message_util.ValidatedMessageFromReq

## Question
Does the aggregation in `ValidatedMessageFromReq` at validated conversion between gateway messages, requests and responses accept a result below the configured threshold, count duplicates, or ignore mismatched payloads, so any internet client with an arbitrary externally-owned key sending signed gateway requests's crafted request yields an unverified answer?

## Target
- File/function: [core/services/gateway/handlers/common/message_util.go](core/services/gateway/handlers/common/message_util.go) -> `ValidatedMessageFromReq`
- Entrypoint: validated conversion between gateway messages, requests and responses
- Attacker controls: response fields echoed from the request (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Craft `response fields echoed from the request` that triggers the weak branch.
- Invariant to test: results must require distinct, verified contributions meeting the threshold
- Expected Immunefi impact: Critical - server credential theft: vault/DKG secret shares or decrypted user secrets disclosed to an unauthorized requester
- Fast validation: table test over the aggregator with duplicate/insufficient inputs
