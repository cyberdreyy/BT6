# Q2063: logged-in check uses the caller's user object in index.ts

## Question
throwIfNotLoggedIn only inspects the user object handed in by the caller; can an attacker pass a fabricated user through privy.crossApp.wallet.* so crossApp wallet barrel binding all three wallet actions to sendCrossAppRequest proceeds without a real session?

## Target
- File/function: [src/action/crossApp/wallet/index.ts](src/action/crossApp/wallet/index.ts) - crossApp wallet barrel binding all three wallet actions to sendCrossAppRequest
- Entrypoint: privy.crossApp.wallet.*
- Attacker controls: shared request pipeline and its response validation
- Exploit idea: Call the wallet action with a hand-built user object and no session.
- Invariant to test: Authorization checks must consult the session, not caller-supplied data.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: call crossApp wallet barrel binding all three wallet actions to sendCrossAppRequest with a fabricated user and no tokens and assert refusal.
