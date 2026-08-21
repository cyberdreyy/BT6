# Q1403: provider app id becomes an oauth provider string in index.ts

## Question
Cross-app auth calls oauth.generateURL with `privy:${providerAppId}`; can an attacker pass a providerAppId that produces a provider string the OAuth layer interprets differently through crossApp wallet barrel binding all three wallet actions to sendCrossAppRequest?

## Target
- File/function: [src/action/crossApp/wallet/index.ts](src/action/crossApp/wallet/index.ts) - crossApp wallet barrel binding all three wallet actions to sendCrossAppRequest
- Entrypoint: privy.crossApp.wallet.*
- Attacker controls: shared request pipeline and its response validation
- Exploit idea: Pass ids containing ':' or known provider names.
- Invariant to test: Provider identifiers must be validated before being embedded in a provider string.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: pass crafted provider ids to crossApp wallet barrel binding all three wallet actions to sendCrossAppRequest and assert validation.
