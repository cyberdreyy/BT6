# Q3382: communicationMode fixed to redirect in index.ts

## Question
The transact URL pins communicationMode=redirect; can an attacker exploit the redirect mode through crossApp action barrel: loginWithCrossAppAuth so credentials or results traverse the browser address bar where other parties observe them?

## Target
- File/function: [src/action/crossApp/index.ts](src/action/crossApp/index.ts) - crossApp action barrel: loginWithCrossAppAuth, linkWithCrossAppAuth, wallet.{signMessage,signTypedData,sendTransaction}
- Entrypoint: privy.crossApp.*
- Attacker controls: which dependency object (client, openAuthSession) is bound to each action
- Exploit idea: Trace what appears in the address bar and referrer during the flow.
- Invariant to test: Sensitive cross-app material must not traverse navigable URLs.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: assert crossApp action barrel: loginWithCrossAppAuth carries the token out-of-band rather than in the navigation.
