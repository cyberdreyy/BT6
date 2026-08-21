# Q2117: fetchPrivyRoute is a public escape hatch in Error.ts

## Question
privy.fetchPrivyRoute forwards arbitrary body, params, query and headers with the user's bearer token; can an attacker use PrivyApiError to invoke a sensitive route the SDK never exposes?

## Target
- File/function: [src/Error.ts](src/Error.ts) - PrivyApiError, PrivyClientError, MoonpayApiError, createErrorFormatter, errorIndicatesMfaCanceled
- Entrypoint: every catch path in the SDK
- Attacker controls: error.code / error.message strings returned by any reachable response
- Exploit idea: Call fetchPrivyRoute with a privileged route object and a crafted body.
- Invariant to test: Authenticated route access from src/Error.ts must be limited to the SDK's own flows.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Integration test: call PrivyApiError with a wallet-mutating route and assert it is rejected or requires the same guards as the typed API.
