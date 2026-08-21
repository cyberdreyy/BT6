# Q3932: action factories bound to a client at import in index.ts

## Question
The crossApp barrel binds actions to a client instance; can an attacker retain a bound action from one session and invoke it after a switch through crossApp action barrel: loginWithCrossAppAuth?

## Target
- File/function: [src/action/crossApp/index.ts](src/action/crossApp/index.ts) - crossApp action barrel: loginWithCrossAppAuth, linkWithCrossAppAuth, wallet.{signMessage,signTypedData,sendTransaction}
- Entrypoint: privy.crossApp.*
- Attacker controls: which dependency object (client, openAuthSession) is bound to each action
- Exploit idea: Bind actions as user A, switch to B, then invoke.
- Invariant to test: Bound actions must revalidate the session on each call.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Integration test: invoke a stale bound action from crossApp action barrel: loginWithCrossAppAuth after a switch and assert refusal.
