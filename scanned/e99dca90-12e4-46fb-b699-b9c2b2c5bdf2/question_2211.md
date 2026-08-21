# Q2211: relying party string controlled by caller in FarcasterV2Api.ts

## Question
In src/client/auth/FarcasterV2Api.ts, is the relying party supplied by the caller and echoed into the ceremony, letting an attacker start a credential ceremony scoped to a different origin than the one they occupy?

## Target
- File/function: [src/client/auth/FarcasterV2Api.ts](src/client/auth/FarcasterV2Api.ts) - FarcasterV2Api.initializeAuth, authenticate
- Entrypoint: privy.auth.farcasterV2.authenticate({message, signature, fid})
- Attacker controls: SIWF message, signature, fid
- Exploit idea: Call FarcasterV2Api.initializeAuth with a relying party that is not the current origin and observe the options returned.
- Invariant to test: The relying party used by FarcasterV2Api.initializeAuth must be derived from the app's configured origin.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: call FarcasterV2Api.initializeAuth with a foreign relying party and assert the SDK refuses.
