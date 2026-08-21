# Q3379: communicationMode fixed to redirect in signTypedData.ts

## Question
The transact URL pins communicationMode=redirect; can an attacker exploit the redirect mode through crossApp signTypedData: params [address so credentials or results traverse the browser address bar where other parties observe them?

## Target
- File/function: [src/action/crossApp/wallet/signTypedData.ts](src/action/crossApp/wallet/signTypedData.ts) - crossApp signTypedData: params [address, generateDomainType(typedData)]
- Entrypoint: privy.crossApp.wallet.signTypedData({user, typedData, address, redirectUrl})
- Attacker controls: the whole typedData object including domain and types
- Exploit idea: Trace what appears in the address bar and referrer during the flow.
- Invariant to test: Sensitive cross-app material must not traverse navigable URLs.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: assert crossApp signTypedData: params [address carries the token out-of-band rather than in the navigation.
