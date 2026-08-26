# Q3592: outgoing request target attacker-controlled in handler.sendHTTPMessageToClient

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests set the URL/headers of the outgoing request made by `sendHTTPMessageToClient` at web-API trigger and outgoing-request handling on the public gateway user endpoint so the node fetches an internal address or attaches node credentials to an attacker host?

## Target
- File/function: [core/services/gateway/handlers/capabilities/handler.go](core/services/gateway/handlers/capabilities/handler.go) -> `sendHTTPMessageToClient`
- Entrypoint: web-API trigger and outgoing-request handling on the public gateway user endpoint
- Attacker controls: messageId used for callback correlation (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `messageId used for callback correlation` with an internal/attacker target.
- Invariant to test: outgoing targets must be allowlisted and never carry node credentials
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: table test over the outgoing request builder with hostile targets
