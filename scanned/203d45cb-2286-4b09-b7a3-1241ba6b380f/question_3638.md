# Q3638: link succeeds against the wrong active user in SiwsApi.ts

## Question
In multi-user mode, can an attacker switch the active user between the request and the refresh inside SiwsApi.fetchNonce so a credential is linked to one account but reported on another?

## Target
- File/function: [src/client/auth/SiwsApi.ts](src/client/auth/SiwsApi.ts) - SiwsApi.fetchNonce, login, link, unlink
- Entrypoint: privy.auth.siws.login({message, signature, walletClientType, connectorType, mode})
- Attacker controls: message string, signature, wallet metadata, nonce reuse
- Exploit idea: Call the link method and switch active user while the request is in flight.
- Invariant to test: A link operation must apply to and report on a single, unchanged user id.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Integration test: switch active user mid-flight and assert SiwsApi.fetchNonce fails rather than reporting success on the new user.
