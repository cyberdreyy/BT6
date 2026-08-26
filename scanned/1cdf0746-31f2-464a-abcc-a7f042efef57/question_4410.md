# Q4410: expired entry cleanup races delivery in http_trigger_handler.HandleUserTriggerRequest

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests time a request at HandleUserTriggerRequest on the public gateway endpoint (workflow HTTP trigger) so cleanup in `HandleUserTriggerRequest` removes an entry mid-delivery and a later response is matched to the attacker's new request?

## Target
- File/function: [core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go](core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go) -> `HandleUserTriggerRequest`
- Entrypoint: HandleUserTriggerRequest on the public gateway endpoint (workflow HTTP trigger)
- Attacker controls: hex casing, length and padding of identifier fields (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Time `hex casing, length and padding of identifier fields` against the expiry sweep.
- Invariant to test: cleanup and delivery must be mutually exclusive per entry
- Expected Immunefi impact: Critical - server credential theft: vault/DKG secret shares or decrypted user secrets disclosed to an unauthorized requester
- Fast validation: concurrency test racing cleanup against delivery
