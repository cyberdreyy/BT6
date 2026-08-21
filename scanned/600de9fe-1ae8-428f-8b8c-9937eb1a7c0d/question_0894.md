# Q0894: update flow accepts mismatched old/new identifiers in GuestApi.ts

## Question
In src/client/auth/GuestApi.ts, can an attacker submit an update request whose old identifier is not the one currently linked, so the code they hold is applied against a different identifier binding?

## Target
- File/function: [src/client/auth/GuestApi.ts](src/client/auth/GuestApi.ts) - GuestApi.create, session.getOrCreateGuestCredential (privy:guest:<appId>)
- Entrypoint: privy.auth.guest.create()
- Attacker controls: guest credential value persisted in localStorage, repeated create calls
- Exploit idea: Call the update method with an arbitrary old value plus a valid code for another identifier and observe client-side acceptance.
- Invariant to test: GuestApi.create must bind the verification code to the exact identifier pair currently on the account.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: call the update method with mismatched old identifier and assert the SDK does not issue the request.
