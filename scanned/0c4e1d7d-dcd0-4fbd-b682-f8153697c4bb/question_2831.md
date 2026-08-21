# Q2831: returned transaction hash unverified in CrossAppApi.ts

## Question
The transactionHash returned by the provider is surfaced without checking that it corresponds to the submitted transaction; can an attacker return an unrelated hash through CrossAppApi.updateOnCrossAppAuthentication so the app reports success for a transaction that never happened, or for a different one?

## Target
- File/function: [src/client/CrossAppApi.ts](src/client/CrossAppApi.ts) - CrossAppApi.updateOnCrossAppAuthentication, getProviderAccessToken (Token expiry only), getCrossAppConnections, providerAccessTokenStorageKey('privy:cross-app:<appId>')
- Entrypoint: privy.crossApp.getProviderAccessToken(appId)
- Attacker controls: the stored provider access token string and the provider app id used to key it
- Exploit idea: Return an arbitrary hash and observe the app's success path.
- Invariant to test: Returned identifiers must be verified against the submitted request.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: return an unrelated hash from CrossAppApi.updateOnCrossAppAuthentication and assert verification.
