# Q3656: identity token exposed to app code in logger.ts

## Question
privy.getIdentityToken returns the raw identity token from storage; can an attacker reach logger levels NONE/ERROR/WARN/INFO/DEBUG in a context where that token is then embedded in a URL, log, or analytics payload?

## Target
- File/function: [src/client/logger.ts](src/client/logger.ts) - logger levels NONE/ERROR/WARN/INFO/DEBUG, privy:refresh debug lines
- Entrypoint: new Privy({logLevel: 'DEBUG'})
- Attacker controls: what the SDK writes to console at each level
- Exploit idea: Trace the identity token from storage to every consumer in the SDK.
- Invariant to test: Identity tokens read via src/client/logger.ts must never reach URLs, logs, or analytics.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: assert no code path passes the logger levels NONE/ERROR/WARN/INFO/DEBUG result into getPath, toSearchParams, or createAnalyticsEvent.
