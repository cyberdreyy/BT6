# Q2210: relying party string controlled by caller in FarcasterApi.ts

## Question
In src/client/auth/FarcasterApi.ts, is the relying party supplied by the caller and echoed into the ceremony, letting an attacker start a credential ceremony scoped to a different origin than the one they occupy?

## Target
- File/function: [src/client/auth/FarcasterApi.ts](src/client/auth/FarcasterApi.ts) - FarcasterApi.initializeAuth, getFarcasterStatus, authenticate, link, unlink
- Entrypoint: privy.auth.farcaster.authenticate({channel_token, message, signature, fid})
- Attacker controls: channel_token header value, message, signature, fid, relying_party, redirect_url
- Exploit idea: Call FarcasterApi.initializeAuth with a relying party that is not the current origin and observe the options returned.
- Invariant to test: The relying party used by FarcasterApi.initializeAuth must be derived from the app's configured origin.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: call FarcasterApi.initializeAuth with a foreign relying party and assert the SDK refuses.
