# Q3929: action factories bound to a client at import in signTypedData.ts

## Question
The crossApp barrel binds actions to a client instance; can an attacker retain a bound action from one session and invoke it after a switch through crossApp signTypedData: params [address?

## Target
- File/function: [src/action/crossApp/wallet/signTypedData.ts](src/action/crossApp/wallet/signTypedData.ts) - crossApp signTypedData: params [address, generateDomainType(typedData)]
- Entrypoint: privy.crossApp.wallet.signTypedData({user, typedData, address, redirectUrl})
- Attacker controls: the whole typedData object including domain and types
- Exploit idea: Bind actions as user A, switch to B, then invoke.
- Invariant to test: Bound actions must revalidate the session on each call.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Integration test: invoke a stale bound action from crossApp signTypedData: params [address after a switch and assert refusal.
