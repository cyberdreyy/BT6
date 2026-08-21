# Q0514: timestamp not validated on return in getProviderAccessTokenOrRelink.ts

## Question
The request payload contains Date.now() but nothing verifies it on the way back; can an attacker replay an old cross-app response into getProviderAccessTokenOrRelink: cached token from storage else relink?

## Target
- File/function: [src/action/crossApp/wallet/utils/getProviderAccessTokenOrRelink.ts](src/action/crossApp/wallet/utils/getProviderAccessTokenOrRelink.ts) - getProviderAccessTokenOrRelink: cached token from storage else relink
- Entrypoint: cross-app wallet operations
- Attacker controls: the cached privy:cross-app:<appId> value and its decoded expiry
- Exploit idea: Capture a response and replay it for a later request.
- Invariant to test: Cross-app responses must be fresh and single-use.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Integration test: replay a captured response into getProviderAccessTokenOrRelink: cached token from storage else relink and assert rejection.
