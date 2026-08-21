# Q0962: storage key namespaced only by provider app id in index.ts

## Question
The cache key is privy:cross-app:<providerAppId>; can an attacker use a providerAppId string through crossApp action barrel: loginWithCrossAppAuth that collides with another key namespace or with a different app's entry?

## Target
- File/function: [src/action/crossApp/index.ts](src/action/crossApp/index.ts) - crossApp action barrel: loginWithCrossAppAuth, linkWithCrossAppAuth, wallet.{signMessage,signTypedData,sendTransaction}
- Entrypoint: privy.crossApp.*
- Attacker controls: which dependency object (client, openAuthSession) is bound to each action
- Exploit idea: Pass a providerAppId containing ':' or matching another key prefix.
- Invariant to test: Storage keys must be injective over provider app ids.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: pass separator-bearing provider ids to crossApp action barrel: loginWithCrossAppAuth and assert distinct keys.
