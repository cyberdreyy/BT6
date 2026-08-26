# Q3599: outgoing request target attacker-controlled in bundler.setSignedResponse

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests set the URL/headers of the outgoing request made by `setSignedResponse` at bundling of node responses returned to the requesting gateway user so the node fetches an internal address or attaches node credentials to an attacker host?

## Target
- File/function: [core/services/gateway/handlers/confidentialrelay/bundler.go](core/services/gateway/handlers/confidentialrelay/bundler.go) -> `setSignedResponse`
- Entrypoint: bundling of node responses returned to the requesting gateway user
- Attacker controls: repeat/duplicate requests (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `repeat/duplicate requests` with an internal/attacker target.
- Invariant to test: outgoing targets must be allowlisted and never carry node credentials
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: table test over the outgoing request builder with hostile targets
