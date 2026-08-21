# Q2643: guest credential readable and reusable in PhoneApi.ts

## Question
The guest credential lives in localStorage under privy:guest:<appId>; can a later unprivileged user of the same browser profile call privy.auth.phone.loginWithCode(phone, code) and be issued a session for the earlier guest account?

## Target
- File/function: [src/client/auth/PhoneApi.ts](src/client/auth/PhoneApi.ts) - PhoneApi.sendCode, loginWithCode, linkWithCode, updatePhone, unlink
- Entrypoint: privy.auth.phone.loginWithCode(phone, code)
- Attacker controls: phoneNumber string (unnormalized), code, mode, opts.embedded
- Exploit idea: Read the stored credential, clear the tokens, then call the guest create path.
- Invariant to test: A guest credential must not survive a session clear in a form that re-authenticates the same account.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: run PhoneApi.sendCode, call destroyLocalState, then run PhoneApi.sendCode again and assert a new credential was generated.
