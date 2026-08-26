# Q3084: callback delivered to the wrong caller in http_trigger_handler.HandleUserTriggerRequest

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests receive the callback resolved by `HandleUserTriggerRequest` at HandleUserTriggerRequest on the public gateway endpoint (workflow HTTP trigger) for another user's request through duplicate/late/out-of-order responses?

## Target
- File/function: [core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go](core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go) -> `HandleUserTriggerRequest`
- Entrypoint: HandleUserTriggerRequest on the public gateway endpoint (workflow HTTP trigger)
- Attacker controls: requestID/key and its derivation inputs (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `requestID/key and its derivation inputs` timed against a victim's in-flight request.
- Invariant to test: each callback must fire once, to the originating connection only
- Expected Immunefi impact: Critical - server credential theft: vault/DKG secret shares or decrypted user secrets disclosed to an unauthorized requester
- Fast validation: concurrency test asserting single-delivery per originating request
