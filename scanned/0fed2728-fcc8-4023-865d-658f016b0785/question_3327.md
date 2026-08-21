# Q3327: cookie names collide across apps in Error.ts

## Question
Cookie names are app-agnostic (privy-token, privy-session); can an attacker on a sibling subdomain of the same registrable domain observe or overwrite them so PrivyApiError reads a foreign credential?

## Target
- File/function: [src/Error.ts](src/Error.ts) - PrivyApiError, PrivyClientError, MoonpayApiError, createErrorFormatter, errorIndicatesMfaCanceled
- Entrypoint: every catch path in the SDK
- Attacker controls: error.code / error.message strings returned by any reachable response
- Exploit idea: Set a cookie of the same name from a sibling context and read it back.
- Invariant to test: Credential cookies read by src/Error.ts must be namespaced and validated before use.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: seed a foreign privy-token cookie and assert PrivyApiError validates the subject before use.
