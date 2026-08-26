# Q0543: cached response served to a different requester in http_trigger_handler.NewHTTPTriggerHandler

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests obtain a cached response produced for another user because the cache key computed near `NewHTTPTriggerHandler` at HandleUserTriggerRequest on the public gateway endpoint (workflow HTTP trigger) omits the sender or authorization context?

## Target
- File/function: [core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go](core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go) -> `NewHTTPTriggerHandler`
- Entrypoint: HandleUserTriggerRequest on the public gateway endpoint (workflow HTTP trigger)
- Attacker controls: hex casing, length and padding of identifier fields (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Repeat `hex casing, length and padding of identifier fields` with the victim's request fields.
- Invariant to test: cache keys must include the authenticated sender and authorization inputs
- Expected Immunefi impact: Critical - server credential theft: vault/DKG secret shares or decrypted user secrets disclosed to an unauthorized requester
- Fast validation: table test asserting cache isolation across senders
