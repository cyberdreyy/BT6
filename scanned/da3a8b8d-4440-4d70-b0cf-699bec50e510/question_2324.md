# Q2324: authenticator response fields copied unchecked in GuestApi.ts

## Question
GuestApi.create's snake-case transformer copies id, raw_id, clientDataJSON, authenticatorData and userHandle straight through; can an attacker submit a response whose user_handle names another account?

## Target
- File/function: [src/client/auth/GuestApi.ts](src/client/auth/GuestApi.ts) - GuestApi.create, session.getOrCreateGuestCredential (privy:guest:<appId>)
- Entrypoint: privy.auth.guest.create()
- Attacker controls: guest credential value persisted in localStorage, repeated create calls
- Exploit idea: Assemble an authenticator response object by hand and pass it to the login method.
- Invariant to test: src/client/auth/GuestApi.ts must not forward an assertion whose handle disagrees with the challenge it requested.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: pass a response with a foreign user_handle and assert the SDK rejects before the network call.
