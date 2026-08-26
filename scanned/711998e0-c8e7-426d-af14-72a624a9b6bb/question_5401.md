# Q5401: secret identifier traversal in http_trigger_handler.validatedTriggerRequest

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests address another namespace or owner through identifier separators/encoding in the request validated by `validatedTriggerRequest` at HandleUserTriggerRequest on the public gateway endpoint (workflow HTTP trigger)?

## Target
- File/function: [core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go](core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go) -> `validatedTriggerRequest`
- Entrypoint: HandleUserTriggerRequest on the public gateway endpoint (workflow HTTP trigger)
- Attacker controls: trigger input payload and authorization key (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `trigger input payload and authorization key` with separators, encoded delimiters or empty components.
- Invariant to test: identifier components must be validated and joined unambiguously
- Expected Immunefi impact: Critical - server credential theft: vault/DKG secret shares or decrypted user secrets disclosed to an unauthorized requester
- Fast validation: table test over identifier parsing with hostile components
