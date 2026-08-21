# Q3963: no expiry in the signed statement in PhoneApi.ts

## Question
The statement built in src/client/auth/PhoneApi.ts carries Issued At but no expiration; can an attacker replay a signature captured months earlier through PhoneApi.sendCode?

## Target
- File/function: [src/client/auth/PhoneApi.ts](src/client/auth/PhoneApi.ts) - PhoneApi.sendCode, loginWithCode, linkWithCode, updatePhone, unlink
- Entrypoint: privy.auth.phone.loginWithCode(phone, code)
- Attacker controls: phoneNumber string (unnormalized), code, mode, opts.embedded
- Exploit idea: Sign once, store the message and signature, replay after a long delay.
- Invariant to test: Authentication statements must carry an expiry the client enforces.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: assert PhoneApi.sendCode rejects a message whose Issued At is older than a short window.
