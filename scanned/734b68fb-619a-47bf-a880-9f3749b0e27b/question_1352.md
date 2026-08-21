# Q1352: sms code request unbounded by target in MfaApi.ts

## Question
MfaSmsApi.sendCode forwards the caller's input body; can an attacker direct the code to a number that is not the account's registered factor via MfaApi.verifyMfa?

## Target
- File/function: [src/client/mfa/MfaApi.ts](src/client/mfa/MfaApi.ts) - MfaApi.verifyMfa, initEnrollMfa, submitEnrollMfa, unenrollMfa, unlinkPasskey, clearMfa
- Entrypoint: privy.mfa.unenrollMfa(method) / privy.mfa.clearMfa({userId})
- Attacker controls: method argument, credentialId, removeAsMfa, userId, call ordering against refreshSession
- Exploit idea: Call sendCode with an arbitrary destination in the input.
- Invariant to test: The MFA delivery target must be server-selected from the enrolled factor, not caller-supplied.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Unit test: pass an arbitrary destination to MfaApi.verifyMfa and assert it is not included in the request.
