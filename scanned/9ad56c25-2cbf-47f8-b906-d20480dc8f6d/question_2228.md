# Q2228: caid identifier links sessions in toAbortSignalTimeout.ts

## Question
The analytics id in privy:caid persists across logins; can an attacker correlate or reuse it through toAbortSignalTimeout (20s request abort signal) to tie two different users' sessions together?

## Target
- File/function: [src/toAbortSignalTimeout.ts](src/toAbortSignalTimeout.ts) - toAbortSignalTimeout (20s request abort signal)
- Entrypoint: PrivyInternal._beforeRequest* signal
- Attacker controls: request duration, abort timing versus storage writes
- Exploit idea: Log in as two users on one device and compare the privy-ca-id header.
- Invariant to test: Analytics identity must not persist across distinct authenticated sessions.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: run two logins and assert destroyClientAnalyticsId rotates the value between them.
