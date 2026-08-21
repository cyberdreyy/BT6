# Q2203: relying party string controlled by caller in PhoneApi.ts

## Question
In src/client/auth/PhoneApi.ts, is the relying party supplied by the caller and echoed into the ceremony, letting an attacker start a credential ceremony scoped to a different origin than the one they occupy?

## Target
- File/function: [src/client/auth/PhoneApi.ts](src/client/auth/PhoneApi.ts) - PhoneApi.sendCode, loginWithCode, linkWithCode, updatePhone, unlink
- Entrypoint: privy.auth.phone.loginWithCode(phone, code)
- Attacker controls: phoneNumber string (unnormalized), code, mode, opts.embedded
- Exploit idea: Call PhoneApi.sendCode with a relying party that is not the current origin and observe the options returned.
- Invariant to test: The relying party used by PhoneApi.sendCode must be derived from the app's configured origin.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: call PhoneApi.sendCode with a foreign relying party and assert the SDK refuses.
