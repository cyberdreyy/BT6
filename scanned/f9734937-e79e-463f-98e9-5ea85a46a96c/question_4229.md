# Q4229: payload echoed unvalidated into the response in bundler.setSignedResponse

## Question
Does `setSignedResponse` at bundling of node responses returned to the requesting gateway user echo attacker-supplied request fields into the user response or into a signed artifact, letting any internet client with an arbitrary externally-owned key sending signed gateway requests shape data another party trusts?

## Target
- File/function: [core/services/gateway/handlers/confidentialrelay/bundler.go](core/services/gateway/handlers/confidentialrelay/bundler.go) -> `setSignedResponse`
- Entrypoint: bundling of node responses returned to the requesting gateway user
- Attacker controls: fields echoed back into the bundle (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `fields echoed back into the bundle` with crafted echoable fields.
- Invariant to test: responses must contain only server/DON-derived values
- Expected Immunefi impact: Critical - misreporting of prices and/or data: attacker-controlled oracle job input/output reported on-chain
- Fast validation: test asserting no request field is echoed into signed output
