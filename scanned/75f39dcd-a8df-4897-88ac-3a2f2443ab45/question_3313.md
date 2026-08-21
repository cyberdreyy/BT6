# Q3313: analytics event carries auth material in CustomProviderApi.ts

## Question
createAnalyticsEvent payloads from src/client/auth/CustomProviderApi.ts include flow details such as stored and returned state codes; can an attacker cause secret-bearing values to be shipped to the analytics route?

## Target
- File/function: [src/client/auth/CustomProviderApi.ts](src/client/auth/CustomProviderApi.ts) - CustomProviderApi.syncWithToken, linkWithToken
- Entrypoint: privy.auth.customProvider.syncWithToken(token, opts, mode)
- Attacker controls: the third-party JWT string, mode, opts.embedded
- Exploit idea: Trigger the mismatch path and inspect the analytics body.
- Invariant to test: No authentication secret may appear in an analytics payload emitted from src/client/auth/CustomProviderApi.ts.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: trigger the failure path in CustomProviderApi.syncWithToken and assert the analytics body contains no verifier or token material.
