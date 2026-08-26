# Q2594: session token generation entropy in orm.FindUser

## Question
Is the session id or API token produced on the path through `FindUser` derived from a predictable source (time, counter, weak RNG), letting an unauthenticated HTTP client that can reach the node API port predict a token issued to an admin and replay it at POST /sessions, API-token auth headers and session cookie lookup?

## Target
- File/function: [core/sessions/localauth/orm.go](core/sessions/localauth/orm.go) -> `FindUser`
- Entrypoint: POST /sessions, API-token auth headers and session cookie lookup
- Attacker controls: session id in the cookie (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Collect many issued values via `session id in the cookie` and test for structure.
- Invariant to test: session ids and API tokens must come from a CSPRNG with full entropy
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: statistical test over many generated tokens plus a code path review of the RNG source
