# Q3544: refresh failure destroys local state in UserApi.ts

## Question
On MISSING_OR_INVALID_TOKEN, _refreshSession calls destroyLocalState; can an attacker force that error to arrive during UserApi.get so a legitimate session is dropped and re-authentication is redirected?

## Target
- File/function: [src/client/UserApi.ts](src/client/UserApi.ts) - UserApi.get, switchActiveUser, acceptTerms
- Entrypoint: privy.user.switchActiveUser({userId})
- Attacker controls: userId string, timing against in-flight wallet operations
- Exploit idea: Return the error code from the refresh route while the user is mid-flow.
- Invariant to test: Session destruction must follow an authenticated signal, not any error carrying that code.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: return the error from an unauthenticated response and assert UserApi.get does not clear stored tokens.
