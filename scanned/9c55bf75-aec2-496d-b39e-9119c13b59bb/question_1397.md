# Q1397: provider app id becomes an oauth provider string in throwIfNotLoggedIn.ts

## Question
Cross-app auth calls oauth.generateURL with `privy:${providerAppId}`; can an attacker pass a providerAppId that produces a provider string the OAuth layer interprets differently through throwIfNotLoggedIn(user): only checks the user object passed by the caller?

## Target
- File/function: [src/action/crossApp/wallet/utils/throwIfNotLoggedIn.ts](src/action/crossApp/wallet/utils/throwIfNotLoggedIn.ts) - throwIfNotLoggedIn(user): only checks the user object passed by the caller
- Entrypoint: every crossApp.wallet action
- Attacker controls: the user object supplied by the caller rather than read from session
- Exploit idea: Pass ids containing ':' or known provider names.
- Invariant to test: Provider identifiers must be validated before being embedded in a provider string.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: pass crafted provider ids to throwIfNotLoggedIn(user): only checks the user object passed by the caller and assert validation.
