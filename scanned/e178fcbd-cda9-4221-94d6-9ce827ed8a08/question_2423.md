# Q2423: challenge not bound to the stored options in PhoneApi.ts

## Question
Does PhoneApi.sendCode accept a challenge argument supplied by the caller rather than the one returned by the matching options call, enabling replay of a previously captured assertion?

## Target
- File/function: [src/client/auth/PhoneApi.ts](src/client/auth/PhoneApi.ts) - PhoneApi.sendCode, loginWithCode, linkWithCode, updatePhone, unlink
- Entrypoint: privy.auth.phone.loginWithCode(phone, code)
- Attacker controls: phoneNumber string (unnormalized), code, mode, opts.embedded
- Exploit idea: Call the options method, discard the challenge, and log in with an older challenge plus its captured assertion.
- Invariant to test: The challenge submitted must be the one issued for this ceremony.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: pass a stale challenge to PhoneApi.sendCode and assert it is rejected.
