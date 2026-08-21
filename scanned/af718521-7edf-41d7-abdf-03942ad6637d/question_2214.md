# Q2214: relying party string controlled by caller in GuestApi.ts

## Question
In src/client/auth/GuestApi.ts, is the relying party supplied by the caller and echoed into the ceremony, letting an attacker start a credential ceremony scoped to a different origin than the one they occupy?

## Target
- File/function: [src/client/auth/GuestApi.ts](src/client/auth/GuestApi.ts) - GuestApi.create, session.getOrCreateGuestCredential (privy:guest:<appId>)
- Entrypoint: privy.auth.guest.create()
- Attacker controls: guest credential value persisted in localStorage, repeated create calls
- Exploit idea: Call GuestApi.create with a relying party that is not the current origin and observe the options returned.
- Invariant to test: The relying party used by GuestApi.create must be derived from the app's configured origin.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: call GuestApi.create with a foreign relying party and assert the SDK refuses.
