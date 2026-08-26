# Q1169: secret ownership check on the wrong field in webapi.Validate

## Question
Does the ownership check for a vault secret in `Validate` at the web-API capability handler config validation and request path from the public gateway endpoint use a request field rather than the recovered signer, letting any internet client with an arbitrary externally-owned key sending signed gateway requests read or overwrite another owner's secret?

## Target
- File/function: [core/services/gateway/handlers/capabilities/webapi.go](core/services/gateway/handlers/capabilities/webapi.go) -> `Validate`
- Entrypoint: the web-API capability handler config validation and request path from the public gateway endpoint
- Attacker controls: rate/quota-relevant fields (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `rate/quota-relevant fields` naming the victim's owner/namespace.
- Invariant to test: secret access must be authorized against the recovered signer only
- Expected Immunefi impact: Critical - server credential theft: vault/DKG secret shares or decrypted user secrets disclosed to an unauthorized requester
- Fast validation: table test reading a foreign owner's secret
