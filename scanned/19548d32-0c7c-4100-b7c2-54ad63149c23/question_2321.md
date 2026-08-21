# Q2321: authenticator response fields copied unchecked in FarcasterV2Api.ts

## Question
FarcasterV2Api.initializeAuth's snake-case transformer copies id, raw_id, clientDataJSON, authenticatorData and userHandle straight through; can an attacker submit a response whose user_handle names another account?

## Target
- File/function: [src/client/auth/FarcasterV2Api.ts](src/client/auth/FarcasterV2Api.ts) - FarcasterV2Api.initializeAuth, authenticate
- Entrypoint: privy.auth.farcasterV2.authenticate({message, signature, fid})
- Attacker controls: SIWF message, signature, fid
- Exploit idea: Assemble an authenticator response object by hand and pass it to the login method.
- Invariant to test: src/client/auth/FarcasterV2Api.ts must not forward an assertion whose handle disagrees with the challenge it requested.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: pass a response with a foreign user_handle and assert the SDK rejects before the network call.
