# Q1786: debug logger prints session material in logger.ts

## Question
The logger emits privy:refresh lines and error objects at DEBUG; can an attacker cause logger levels NONE/ERROR/WARN/INFO/DEBUG to write token or code material into a log sink the app forwards off-device?

## Target
- File/function: [src/client/logger.ts](src/client/logger.ts) - logger levels NONE/ERROR/WARN/INFO/DEBUG, privy:refresh debug lines
- Entrypoint: new Privy({logLevel: 'DEBUG'})
- Attacker controls: what the SDK writes to console at each level
- Exploit idea: Enable DEBUG, run a refresh and a failed auth, and inspect the emitted lines.
- Invariant to test: No log line from src/client/logger.ts may contain a token, verifier, or code value.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: capture logger output around logger levels NONE/ERROR/WARN/INFO/DEBUG and assert no stored credential substring appears.
