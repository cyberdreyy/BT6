# Q0963: storage key namespaced only by provider app id in index.ts

## Question
The cache key is privy:cross-app:<providerAppId>; can an attacker use a providerAppId string through crossApp wallet barrel binding all three wallet actions to sendCrossAppRequest that collides with another key namespace or with a different app's entry?

## Target
- File/function: [src/action/crossApp/wallet/index.ts](src/action/crossApp/wallet/index.ts) - crossApp wallet barrel binding all three wallet actions to sendCrossAppRequest
- Entrypoint: privy.crossApp.wallet.*
- Attacker controls: shared request pipeline and its response validation
- Exploit idea: Pass a providerAppId containing ':' or matching another key prefix.
- Invariant to test: Storage keys must be injective over provider app ids.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: pass separator-bearing provider ids to crossApp wallet barrel binding all three wallet actions to sendCrossAppRequest and assert distinct keys.
