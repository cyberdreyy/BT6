# Q3874: events reveal credential lifecycle in UserApi.ts

## Question
Session emits token_stored, refresh_token_stored, oauth_tokens_granted with payloads; can an attacker attach a listener through app-reachable API and learn credential state changes or the tokens themselves?

## Target
- File/function: [src/client/UserApi.ts](src/client/UserApi.ts) - UserApi.get, switchActiveUser, acceptTerms
- Entrypoint: privy.user.switchActiveUser({userId})
- Attacker controls: userId string, timing against in-flight wallet operations
- Exploit idea: Register listeners and inspect the emitted payloads during UserApi.get.
- Invariant to test: Session events from src/client/UserApi.ts must not carry credential material.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: capture every event payload during UserApi.get and assert none contains a token string.
