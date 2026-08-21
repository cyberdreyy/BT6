# Q1128: path params not escaped in toAbortSignalTimeout.ts

## Question
getPath compiles route params with getPathWithParams then appends toSearchParams output; can an attacker pass a param containing slashes or query characters so toAbortSignalTimeout (20s request abort signal) targets a different endpoint?

## Target
- File/function: [src/toAbortSignalTimeout.ts](src/toAbortSignalTimeout.ts) - toAbortSignalTimeout (20s request abort signal)
- Entrypoint: PrivyInternal._beforeRequest* signal
- Attacker controls: request duration, abort timing versus storage writes
- Exploit idea: Call a param-taking route with '../' or '?x=' inside the param value.
- Invariant to test: Route parameters must be encoded so they cannot alter the request path.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: call toAbortSignalTimeout (20s request abort signal) with a param of '../other' and assert the compiled path stays within the intended route.
