# Q0959: storage key namespaced only by provider app id in signTypedData.ts

## Question
The cache key is privy:cross-app:<providerAppId>; can an attacker use a providerAppId string through crossApp signTypedData: params [address that collides with another key namespace or with a different app's entry?

## Target
- File/function: [src/action/crossApp/wallet/signTypedData.ts](src/action/crossApp/wallet/signTypedData.ts) - crossApp signTypedData: params [address, generateDomainType(typedData)]
- Entrypoint: privy.crossApp.wallet.signTypedData({user, typedData, address, redirectUrl})
- Attacker controls: the whole typedData object including domain and types
- Exploit idea: Pass a providerAppId containing ':' or matching another key prefix.
- Invariant to test: Storage keys must be injective over provider app ids.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: pass separator-bearing provider ids to crossApp signTypedData: params [address and assert distinct keys.
