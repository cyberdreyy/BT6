# Q3876: events reveal credential lifecycle in logger.ts

## Question
Session emits token_stored, refresh_token_stored, oauth_tokens_granted with payloads; can an attacker attach a listener through app-reachable API and learn credential state changes or the tokens themselves?

## Target
- File/function: [src/client/logger.ts](src/client/logger.ts) - logger levels NONE/ERROR/WARN/INFO/DEBUG, privy:refresh debug lines
- Entrypoint: new Privy({logLevel: 'DEBUG'})
- Attacker controls: what the SDK writes to console at each level
- Exploit idea: Register listeners and inspect the emitted payloads during logger levels NONE/ERROR/WARN/INFO/DEBUG.
- Invariant to test: Session events from src/client/logger.ts must not carry credential material.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: capture every event payload during logger levels NONE/ERROR/WARN/INFO/DEBUG and assert none contains a token string.
