# Q3492: login and link share the same code path in index.ts

## Question
loginWithCrossAppAuth and linkWithCrossAppAuth both call oauth generate/exchange with the same PKCE storage keys; can an attacker interleave them through crossApp action barrel: loginWithCrossAppAuth so a link completes a login or vice versa?

## Target
- File/function: [src/action/crossApp/index.ts](src/action/crossApp/index.ts) - crossApp action barrel: loginWithCrossAppAuth, linkWithCrossAppAuth, wallet.{signMessage,signTypedData,sendTransaction}
- Entrypoint: privy.crossApp.*
- Attacker controls: which dependency object (client, openAuthSession) is bound to each action
- Exploit idea: Start a cross-app login and a cross-app link concurrently.
- Invariant to test: Each cross-app flow must own its PKCE material.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Integration test: interleave both crossApp action barrel: loginWithCrossAppAuth flows and assert the second is rejected.
