# Q0443: mode parameter escalates link into login in PhoneApi.ts

## Question
Can an unprivileged attacker pass a mode value to PhoneApi.sendCode that turns an account-linking action into a login-or-sign-up, so the credential they control becomes a new authenticated session rather than a link on the existing account?

## Target
- File/function: [src/client/auth/PhoneApi.ts](src/client/auth/PhoneApi.ts) - PhoneApi.sendCode, loginWithCode, linkWithCode, updatePhone, unlink
- Entrypoint: privy.auth.phone.loginWithCode(phone, code)
- Attacker controls: phoneNumber string (unnormalized), code, mode, opts.embedded
- Exploit idea: Call privy.auth.phone.loginWithCode(phone, code) with the mode field flipped and inspect which route and which session-update path executes.
- Invariant to test: The mode argument must never let a caller convert a link request into a session-issuing login inside src/client/auth/PhoneApi.ts.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: call PhoneApi.sendCode with each accepted mode and assert updateWithTokensResponse is only reached for genuine login modes.
