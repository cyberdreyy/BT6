# Q2654: guest credential readable and reusable in GuestApi.ts

## Question
The guest credential lives in localStorage under privy:guest:<appId>; can a later unprivileged user of the same browser profile call privy.auth.guest.create() and be issued a session for the earlier guest account?

## Target
- File/function: [src/client/auth/GuestApi.ts](src/client/auth/GuestApi.ts) - GuestApi.create, session.getOrCreateGuestCredential (privy:guest:<appId>)
- Entrypoint: privy.auth.guest.create()
- Attacker controls: guest credential value persisted in localStorage, repeated create calls
- Exploit idea: Read the stored credential, clear the tokens, then call the guest create path.
- Invariant to test: A guest credential must not survive a session clear in a form that re-authenticates the same account.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: run GuestApi.create, call destroyLocalState, then run GuestApi.create again and assert a new credential was generated.
