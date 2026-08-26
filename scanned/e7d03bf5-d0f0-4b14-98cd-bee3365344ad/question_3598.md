# Q3598: outgoing request target attacker-controlled in handler.copiedResponses

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests set the URL/headers of the outgoing request made by `copiedResponses` at HandleJSONRPCUserMessage on the confidential-relay gateway method so the node fetches an internal address or attaches node credentials to an attacker host?

## Target
- File/function: [core/services/gateway/handlers/confidentialrelay/handler.go](core/services/gateway/handlers/confidentialrelay/handler.go) -> `copiedResponses`
- Entrypoint: HandleJSONRPCUserMessage on the confidential-relay gateway method
- Attacker controls: requestID used to key the active request (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `requestID used to key the active request` with an internal/attacker target.
- Invariant to test: outgoing targets must be allowlisted and never carry node credentials
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: table test over the outgoing request builder with hostile targets
