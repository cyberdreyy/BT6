# Q2104: nonce reuse across two logins in GuestApi.ts

## Question
Can an attacker reuse a nonce previously issued by init()/fetchNonce for the same address to authenticate a second time from a different device or context?

## Target
- File/function: [src/client/auth/GuestApi.ts](src/client/auth/GuestApi.ts) - GuestApi.create, session.getOrCreateGuestCredential (privy:guest:<appId>)
- Entrypoint: privy.auth.guest.create()
- Attacker controls: guest credential value persisted in localStorage, repeated create calls
- Exploit idea: Capture the nonce, complete a login, then replay message and signature.
- Invariant to test: Each issued nonce must be single-use for GuestApi.create.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Integration test: complete a login and then replay the same message/signature and assert the second attempt fails.
