# Q3602: outgoing request target attacker-controlled in requestcache.ProcessResponse

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests set the URL/headers of the outgoing request made by `ProcessResponse` at the gateway request cache keyed per user request so the node fetches an internal address or attaches node credentials to an attacker host?

## Target
- File/function: [core/services/gateway/handlers/common/requestcache.go](core/services/gateway/handlers/common/requestcache.go) -> `ProcessResponse`
- Entrypoint: the gateway request cache keyed per user request
- Attacker controls: repeat and concurrent submissions (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `repeat and concurrent submissions` with an internal/attacker target.
- Invariant to test: outgoing targets must be allowlisted and never carry node credentials
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: table test over the outgoing request builder with hostile targets
