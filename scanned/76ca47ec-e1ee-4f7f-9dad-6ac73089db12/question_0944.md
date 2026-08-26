# Q0944: quorum/threshold check weak in handler.addResponseForNode

## Question
Does the aggregation in `addResponseForNode` at the vault gateway methods (secrets create/update/get/list, DKG) on the public user endpoint accept a result below the configured threshold, count duplicates, or ignore mismatched payloads, so any internet client with an arbitrary externally-owned key sending signed gateway requests's crafted request yields an unverified answer?

## Target
- File/function: [core/services/gateway/handlers/vault/handler.go](core/services/gateway/handlers/vault/handler.go) -> `addResponseForNode`
- Entrypoint: the vault gateway methods (secrets create/update/get/list, DKG) on the public user endpoint
- Attacker controls: signature over the request (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Craft `signature over the request` that triggers the weak branch.
- Invariant to test: results must require distinct, verified contributions meeting the threshold
- Expected Immunefi impact: Critical - server credential theft: vault/DKG secret shares or decrypted user secrets disclosed to an unauthorized requester
- Fast validation: table test over the aggregator with duplicate/insufficient inputs
