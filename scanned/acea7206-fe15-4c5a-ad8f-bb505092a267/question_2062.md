# Q2062: logged-in check uses the caller's user object in index.ts

## Question
throwIfNotLoggedIn only inspects the user object handed in by the caller; can an attacker pass a fabricated user through privy.crossApp.* so crossApp action barrel: loginWithCrossAppAuth proceeds without a real session?

## Target
- File/function: [src/action/crossApp/index.ts](src/action/crossApp/index.ts) - crossApp action barrel: loginWithCrossAppAuth, linkWithCrossAppAuth, wallet.{signMessage,signTypedData,sendTransaction}
- Entrypoint: privy.crossApp.*
- Attacker controls: which dependency object (client, openAuthSession) is bound to each action
- Exploit idea: Call the wallet action with a hand-built user object and no session.
- Invariant to test: Authorization checks must consult the session, not caller-supplied data.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: call crossApp action barrel: loginWithCrossAppAuth with a fabricated user and no tokens and assert refusal.
