# Q1553: response correlation by attacker-chosen key in codec.Codec

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests receive another user's response because correlation in `Codec` at the encode/decode boundary for gateway user requests and responses uses a caller-supplied key rather than a server-generated one?

## Target
- File/function: [core/services/gateway/api/codec.go](core/services/gateway/api/codec.go) -> `Codec`
- Entrypoint: the encode/decode boundary for gateway user requests and responses
- Attacker controls: response correlation fields (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `response correlation fields` matching a victim's correlation key.
- Invariant to test: correlation keys must be server-generated and sender-scoped
- Expected Immunefi impact: Critical - server credential theft: vault/DKG secret shares or decrypted user secrets disclosed to an unauthorized requester
- Fast validation: test asserting responses are delivered only to the originating connection
