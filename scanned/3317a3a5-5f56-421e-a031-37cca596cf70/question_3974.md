# Q3974: no expiry in the signed statement in GuestApi.ts

## Question
The statement built in src/client/auth/GuestApi.ts carries Issued At but no expiration; can an attacker replay a signature captured months earlier through GuestApi.create?

## Target
- File/function: [src/client/auth/GuestApi.ts](src/client/auth/GuestApi.ts) - GuestApi.create, session.getOrCreateGuestCredential (privy:guest:<appId>)
- Entrypoint: privy.auth.guest.create()
- Attacker controls: guest credential value persisted in localStorage, repeated create calls
- Exploit idea: Sign once, store the message and signature, replay after a long delay.
- Invariant to test: Authentication statements must carry an expiry the client enforces.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: assert GuestApi.create rejects a message whose Issued At is older than a short window.
