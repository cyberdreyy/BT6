# Q1126: path params not escaped in logger.ts

## Question
getPath compiles route params with getPathWithParams then appends toSearchParams output; can an attacker pass a param containing slashes or query characters so logger levels NONE/ERROR/WARN/INFO/DEBUG targets a different endpoint?

## Target
- File/function: [src/client/logger.ts](src/client/logger.ts) - logger levels NONE/ERROR/WARN/INFO/DEBUG, privy:refresh debug lines
- Entrypoint: new Privy({logLevel: 'DEBUG'})
- Attacker controls: what the SDK writes to console at each level
- Exploit idea: Call a param-taking route with '../' or '?x=' inside the param value.
- Invariant to test: Route parameters must be encoded so they cannot alter the request path.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: call logger levels NONE/ERROR/WARN/INFO/DEBUG with a param of '../other' and assert the compiled path stays within the intended route.
