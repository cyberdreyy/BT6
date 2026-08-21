# Q2116: fetchPrivyRoute is a public escape hatch in logger.ts

## Question
privy.fetchPrivyRoute forwards arbitrary body, params, query and headers with the user's bearer token; can an attacker use logger levels NONE/ERROR/WARN/INFO/DEBUG to invoke a sensitive route the SDK never exposes?

## Target
- File/function: [src/client/logger.ts](src/client/logger.ts) - logger levels NONE/ERROR/WARN/INFO/DEBUG, privy:refresh debug lines
- Entrypoint: new Privy({logLevel: 'DEBUG'})
- Attacker controls: what the SDK writes to console at each level
- Exploit idea: Call fetchPrivyRoute with a privileged route object and a crafted body.
- Invariant to test: Authenticated route access from src/client/logger.ts must be limited to the SDK's own flows.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Integration test: call logger levels NONE/ERROR/WARN/INFO/DEBUG with a wallet-mutating route and assert it is rejected or requires the same guards as the typed API.
