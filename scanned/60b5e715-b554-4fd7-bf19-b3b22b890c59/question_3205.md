# Q3205: response correlation by attacker-chosen key in gateway.setupFromNewConfig

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests receive another user's response because correlation in `setupFromNewConfig` at ProcessRequest on the public gateway user endpoint uses a caller-supplied key rather than a server-generated one?

## Target
- File/function: [core/services/gateway/gateway.go](core/services/gateway/gateway.go) -> `setupFromNewConfig`
- Entrypoint: ProcessRequest on the public gateway user endpoint
- Attacker controls: the message payload (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `message payload` matching a victim's correlation key.
- Invariant to test: correlation keys must be server-generated and sender-scoped
- Expected Immunefi impact: Critical - server credential theft: vault/DKG secret shares or decrypted user secrets disclosed to an unauthorized requester
- Fast validation: test asserting responses are delivered only to the originating connection
