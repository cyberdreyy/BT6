# Q1896: error objects carry response bodies in logger.ts

## Question
PrivyApiError/MoonpayApiError keep code, status and even the raw response; can an attacker surface a thrown error from logger levels NONE/ERROR/WARN/INFO/DEBUG whose payload leaks another user's data or a credential?

## Target
- File/function: [src/client/logger.ts](src/client/logger.ts) - logger levels NONE/ERROR/WARN/INFO/DEBUG, privy:refresh debug lines
- Entrypoint: new Privy({logLevel: 'DEBUG'})
- Attacker controls: what the SDK writes to console at each level
- Exploit idea: Force an error response containing sensitive fields and inspect the thrown object reaching app code.
- Invariant to test: Errors raised from src/client/logger.ts must not carry raw response bodies to app code.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: throw from a route with a sensitive body and assert the error exposes only code and message.
