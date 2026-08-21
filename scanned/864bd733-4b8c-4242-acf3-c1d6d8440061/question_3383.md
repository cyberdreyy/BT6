# Q3383: communicationMode fixed to redirect in index.ts

## Question
The transact URL pins communicationMode=redirect; can an attacker exploit the redirect mode through crossApp wallet barrel binding all three wallet actions to sendCrossAppRequest so credentials or results traverse the browser address bar where other parties observe them?

## Target
- File/function: [src/action/crossApp/wallet/index.ts](src/action/crossApp/wallet/index.ts) - crossApp wallet barrel binding all three wallet actions to sendCrossAppRequest
- Entrypoint: privy.crossApp.wallet.*
- Attacker controls: shared request pipeline and its response validation
- Exploit idea: Trace what appears in the address bar and referrer during the flow.
- Invariant to test: Sensitive cross-app material must not traverse navigable URLs.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: assert crossApp wallet barrel binding all three wallet actions to sendCrossAppRequest carries the token out-of-band rather than in the navigation.
