# Q0784: callback delivered to the wrong caller in handler.addResponseForNode

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests receive the callback resolved by `addResponseForNode` at HandleJSONRPCUserMessage on the confidential-relay gateway method for another user's request through duplicate/late/out-of-order responses?

## Target
- File/function: [core/services/gateway/handlers/confidentialrelay/handler.go](core/services/gateway/handlers/confidentialrelay/handler.go) -> `addResponseForNode`
- Entrypoint: HandleJSONRPCUserMessage on the confidential-relay gateway method
- Attacker controls: the relay request payload and method (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `relay request payload and method` timed against a victim's in-flight request.
- Invariant to test: each callback must fire once, to the originating connection only
- Expected Immunefi impact: Critical - server credential theft: vault/DKG secret shares or decrypted user secrets disclosed to an unauthorized requester
- Fast validation: concurrency test asserting single-delivery per originating request
