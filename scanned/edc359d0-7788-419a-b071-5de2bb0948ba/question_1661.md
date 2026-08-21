# Q1661: redirect target chosen by caller in FarcasterV2Api.ts

## Question
Can an attacker pass a redirect_to value into FarcasterV2Api.initializeAuth that sends the authorization code to an origin they control while the SDK still treats the resulting callback as trusted?

## Target
- File/function: [src/client/auth/FarcasterV2Api.ts](src/client/auth/FarcasterV2Api.ts) - FarcasterV2Api.initializeAuth, authenticate
- Entrypoint: privy.auth.farcasterV2.authenticate({message, signature, fid})
- Attacker controls: SIWF message, signature, fid
- Exploit idea: Call generateURL with an attacker origin and complete loginWithCode with the code delivered there.
- Invariant to test: src/client/auth/FarcasterV2Api.ts must not accept a redirect target that is unrelated to the app's configured origins.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: call FarcasterV2Api.initializeAuth with an off-origin redirect_to and assert the request is rejected client-side.
