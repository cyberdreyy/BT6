# Q1016: credentials include on every request in logger.ts

## Question
_beforeRequest* sets credentials: 'include' with Authorization on all routes; can an attacker reach logger levels NONE/ERROR/WARN/INFO/DEBUG with a route/params combination that sends cookies and bearer tokens to an unintended path?

## Target
- File/function: [src/client/logger.ts](src/client/logger.ts) - logger levels NONE/ERROR/WARN/INFO/DEBUG, privy:refresh debug lines
- Entrypoint: new Privy({logLevel: 'DEBUG'})
- Attacker controls: what the SDK writes to console at each level
- Exploit idea: Call privy.fetchPrivyRoute with a route object whose path template resolves outside the intended API surface.
- Invariant to test: Authenticated requests from src/client/logger.ts must only be issued to the compiled, trusted route set.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: pass a hand-built route to logger levels NONE/ERROR/WARN/INFO/DEBUG and assert path compilation rejects it.
