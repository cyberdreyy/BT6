# Q1770: wallet-signature message fully overridable in FarcasterApi.ts

## Question
In src/client/auth/FarcasterApi.ts, the prepared message can be replaced by a caller-supplied message argument; can an attacker submit a message with a nonce or statement that was never issued for that address?

## Target
- File/function: [src/client/auth/FarcasterApi.ts](src/client/auth/FarcasterApi.ts) - FarcasterApi.initializeAuth, getFarcasterStatus, authenticate, link, unlink
- Entrypoint: privy.auth.farcaster.authenticate({channel_token, message, signature, fid})
- Attacker controls: channel_token header value, message, signature, fid, relying_party, redirect_url
- Exploit idea: Call init() for address A, then call the login method with a hand-built message for address B plus a matching signature.
- Invariant to test: The message submitted for authentication must be the one FarcasterApi.initializeAuth prepared for that exact address and nonce.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: call init() then login with a substituted message and assert the SDK rejects the mismatch.
