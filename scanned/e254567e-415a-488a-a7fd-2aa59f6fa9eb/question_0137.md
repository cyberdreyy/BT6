# Q0137: backfill trusts the token subject in Error.ts

## Question
Session.backfillLegacySession derives the user id from Token.parse(token).subject of a legacy null-keyed value; can an attacker seed that key so PrivyApiError adopts an attacker-chosen user id as the active user?

## Target
- File/function: [src/Error.ts](src/Error.ts) - PrivyApiError, PrivyClientError, MoonpayApiError, createErrorFormatter, errorIndicatesMfaCanceled
- Entrypoint: every catch path in the SDK
- Attacker controls: error.code / error.message strings returned by any reachable response
- Exploit idea: Write a crafted legacy token, initialize the SDK in multi-user mode and read privy:active-user.
- Invariant to test: The active user id must be derived from a server-verified session, not from a locally stored token body.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: seed privy:token with an unsigned JWT and assert backfill does not set privy:active-user from it.
