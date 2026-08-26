# Q2163: payload echoed unvalidated into the response in webapi.Validate

## Question
Does `Validate` at the web-API capability handler config validation and request path from the public gateway endpoint echo attacker-supplied request fields into the user response or into a signed artifact, letting any internet client with an arbitrary externally-owned key sending signed gateway requests shape data another party trusts?

## Target
- File/function: [core/services/gateway/handlers/capabilities/webapi.go](core/services/gateway/handlers/capabilities/webapi.go) -> `Validate`
- Entrypoint: the web-API capability handler config validation and request path from the public gateway endpoint
- Attacker controls: the request URL, method and headers in the payload (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `request URL, method and headers in the payload` with crafted echoable fields.
- Invariant to test: responses must contain only server/DON-derived values
- Expected Immunefi impact: Critical - misreporting of prices and/or data: attacker-controlled oracle job input/output reported on-chain
- Fast validation: test asserting no request field is echoed into signed output
