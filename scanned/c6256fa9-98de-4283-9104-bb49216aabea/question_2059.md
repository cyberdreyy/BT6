# Q2059: logged-in check uses the caller's user object in signTypedData.ts

## Question
throwIfNotLoggedIn only inspects the user object handed in by the caller; can an attacker pass a fabricated user through privy.crossApp.wallet.signTypedData({user, typedData, address, redirectUrl}) so crossApp signTypedData: params [address proceeds without a real session?

## Target
- File/function: [src/action/crossApp/wallet/signTypedData.ts](src/action/crossApp/wallet/signTypedData.ts) - crossApp signTypedData: params [address, generateDomainType(typedData)]
- Entrypoint: privy.crossApp.wallet.signTypedData({user, typedData, address, redirectUrl})
- Attacker controls: the whole typedData object including domain and types
- Exploit idea: Call the wallet action with a hand-built user object and no session.
- Invariant to test: Authorization checks must consult the session, not caller-supplied data.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: call crossApp signTypedData: params [address with a fabricated user and no tokens and assert refusal.
