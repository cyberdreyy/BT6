# Q3633: link succeeds against the wrong active user in PhoneApi.ts

## Question
In multi-user mode, can an attacker switch the active user between the request and the refresh inside PhoneApi.sendCode so a credential is linked to one account but reported on another?

## Target
- File/function: [src/client/auth/PhoneApi.ts](src/client/auth/PhoneApi.ts) - PhoneApi.sendCode, loginWithCode, linkWithCode, updatePhone, unlink
- Entrypoint: privy.auth.phone.loginWithCode(phone, code)
- Attacker controls: phoneNumber string (unnormalized), code, mode, opts.embedded
- Exploit idea: Call the link method and switch active user while the request is in flight.
- Invariant to test: A link operation must apply to and report on a single, unchanged user id.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Integration test: switch active user mid-flight and assert PhoneApi.sendCode fails rather than reporting success on the new user.
