# Q0954: storage key namespaced only by provider app id in getProviderAccessTokenOrRelink.ts

## Question
The cache key is privy:cross-app:<providerAppId>; can an attacker use a providerAppId string through getProviderAccessTokenOrRelink: cached token from storage else relink that collides with another key namespace or with a different app's entry?

## Target
- File/function: [src/action/crossApp/wallet/utils/getProviderAccessTokenOrRelink.ts](src/action/crossApp/wallet/utils/getProviderAccessTokenOrRelink.ts) - getProviderAccessTokenOrRelink: cached token from storage else relink
- Entrypoint: cross-app wallet operations
- Attacker controls: the cached privy:cross-app:<appId> value and its decoded expiry
- Exploit idea: Pass a providerAppId containing ':' or matching another key prefix.
- Invariant to test: Storage keys must be injective over provider app ids.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: pass separator-bearing provider ids to getProviderAccessTokenOrRelink: cached token from storage else relink and assert distinct keys.
