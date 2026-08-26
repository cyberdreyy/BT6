# Q0547: cached response served to a different requester in handler.addResponseForNode

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests obtain a cached response produced for another user because the cache key computed near `addResponseForNode` at HandleJSONRPCUserMessage on the confidential-relay gateway method omits the sender or authorization context?

## Target
- File/function: [core/services/gateway/handlers/confidentialrelay/handler.go](core/services/gateway/handlers/confidentialrelay/handler.go) -> `addResponseForNode`
- Entrypoint: HandleJSONRPCUserMessage on the confidential-relay gateway method
- Attacker controls: the relay request payload and method (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Repeat `relay request payload and method` with the victim's request fields.
- Invariant to test: cache keys must include the authenticated sender and authorization inputs
- Expected Immunefi impact: Critical - server credential theft: vault/DKG secret shares or decrypted user secrets disclosed to an unauthorized requester
- Fast validation: table test asserting cache isolation across senders
