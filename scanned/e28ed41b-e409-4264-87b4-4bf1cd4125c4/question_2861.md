# Q2861: unlink then relink races the session refresh in AuthApi.ts

## Question
Can an attacker interleave an unlink and a link through AuthApi.logout so refreshSession observes the intermediate state and the app renders a linked-account set that no longer matches the server?

## Target
- File/function: [src/client/auth/AuthApi.ts](src/client/auth/AuthApi.ts) - AuthApi.logout, AuthApi.email/phone/oauth/siwe/siws/passkey sub-APIs
- Entrypoint: privy.auth.logout(), privy.auth.<method>
- Attacker controls: logout timing, userId passed to mfa.clearMfa, concurrent login calls
- Exploit idea: Fire unlink and link back to back and inspect the user object each returns.
- Invariant to test: The user object returned by each src/client/auth/AuthApi.ts operation must reflect the state after that operation completed.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Integration test: run unlink and link concurrently and assert the final returned linked_accounts equals a fresh user.get().
