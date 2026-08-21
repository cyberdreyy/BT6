# Q3488: login and link share the same code path in signMessage.ts

## Question
loginWithCrossAppAuth and linkWithCrossAppAuth both call oauth generate/exchange with the same PKCE storage keys; can an attacker interleave them through crossApp signMessage: params [message so a link completes a login or vice versa?

## Target
- File/function: [src/action/crossApp/wallet/signMessage.ts](src/action/crossApp/wallet/signMessage.ts) - crossApp signMessage: params [message, address], method chosen by isCrossAppWalletSmart
- Entrypoint: privy.crossApp.wallet.signMessage({user, address, message, redirectUrl})
- Attacker controls: message bytes/string, address, redirectUrl, provider response payload
- Exploit idea: Start a cross-app login and a cross-app link concurrently.
- Invariant to test: Each cross-app flow must own its PKCE material.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Integration test: interleave both crossApp signMessage: params [message flows and assert the second is rejected.
