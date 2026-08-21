# Q1399: provider app id becomes an oauth provider string in signTypedData.ts

## Question
Cross-app auth calls oauth.generateURL with `privy:${providerAppId}`; can an attacker pass a providerAppId that produces a provider string the OAuth layer interprets differently through crossApp signTypedData: params [address?

## Target
- File/function: [src/action/crossApp/wallet/signTypedData.ts](src/action/crossApp/wallet/signTypedData.ts) - crossApp signTypedData: params [address, generateDomainType(typedData)]
- Entrypoint: privy.crossApp.wallet.signTypedData({user, typedData, address, redirectUrl})
- Attacker controls: the whole typedData object including domain and types
- Exploit idea: Pass ids containing ':' or known provider names.
- Invariant to test: Provider identifiers must be validated before being embedded in a provider string.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: pass crafted provider ids to crossApp signTypedData: params [address and assert validation.
