# Q4414: expired entry cleanup races delivery in handler.copiedResponses

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests time a request at HandleJSONRPCUserMessage on the confidential-relay gateway method so cleanup in `copiedResponses` removes an entry mid-delivery and a later response is matched to the attacker's new request?

## Target
- File/function: [core/services/gateway/handlers/confidentialrelay/handler.go](core/services/gateway/handlers/confidentialrelay/handler.go) -> `copiedResponses`
- Entrypoint: HandleJSONRPCUserMessage on the confidential-relay gateway method
- Attacker controls: submission timing relative to the quorum grace window (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Time `submission timing relative to the quorum grace window` against the expiry sweep.
- Invariant to test: cleanup and delivery must be mutually exclusive per entry
- Expected Immunefi impact: Critical - server credential theft: vault/DKG secret shares or decrypted user secrets disclosed to an unauthorized requester
- Fast validation: concurrency test racing cleanup against delivery
