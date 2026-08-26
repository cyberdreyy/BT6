# Q3601: outgoing request target attacker-controlled in aggregator.signedResponseRequestIDEnabled

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests set the URL/headers of the outgoing request made by `signedResponseRequestIDEnabled` at aggregation and signature/quorum validation of vault node responses before they reach the requesting user so the node fetches an internal address or attaches node credentials to an attacker host?

## Target
- File/function: [core/services/gateway/handlers/vault/aggregator.go](core/services/gateway/handlers/vault/aggregator.go) -> `signedResponseRequestIDEnabled`
- Entrypoint: aggregation and signature/quorum validation of vault node responses before they reach the requesting user
- Attacker controls: method selection that toggles signed validation (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `method selection that toggles signed validation` with an internal/attacker target.
- Invariant to test: outgoing targets must be allowlisted and never carry node credentials
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: table test over the outgoing request builder with hostile targets
