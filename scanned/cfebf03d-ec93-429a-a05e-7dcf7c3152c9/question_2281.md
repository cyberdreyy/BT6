# Q2281: read_only flag is the only authorization gate in CrossAppApi.ts

## Question
sendCrossAppRequest rejects only when the connection is marked read_only; can an attacker influence the connections response so CrossAppApi.updateOnCrossAppAuthentication treats a read-only connection as transactable?

## Target
- File/function: [src/client/CrossAppApi.ts](src/client/CrossAppApi.ts) - CrossAppApi.updateOnCrossAppAuthentication, getProviderAccessToken (Token expiry only), getCrossAppConnections, providerAccessTokenStorageKey('privy:cross-app:<appId>')
- Entrypoint: privy.crossApp.getProviderAccessToken(appId)
- Attacker controls: the stored provider access token string and the provider app id used to key it
- Exploit idea: Return a connection without the read_only flag.
- Invariant to test: Transaction authority must be established server-side, not by a client-visible flag.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: omit read_only in CrossAppApi.updateOnCrossAppAuthentication's stub and assert the SDK still requires explicit authority.
