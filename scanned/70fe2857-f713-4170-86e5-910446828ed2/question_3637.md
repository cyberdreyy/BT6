# Q3637: link succeeds against the wrong active user in SiweApi.ts

## Question
In multi-user mode, can an attacker switch the active user between the request and the refresh inside SiweApi.init so a credential is linked to one account but reported on another?

## Target
- File/function: [src/client/auth/SiweApi.ts](src/client/auth/SiweApi.ts) - SiweApi.init, loginWithSiwe, linkWithSiwe, unlinkWallet, generateSiweMessage
- Entrypoint: privy.auth.siwe.init(wallet, domain, uri) then loginWithSiwe(signature, wallet, message)
- Attacker controls: domain, uri, chainId, walletClientType, connectorType, full message override, signature
- Exploit idea: Call the link method and switch active user while the request is in flight.
- Invariant to test: A link operation must apply to and report on a single, unchanged user id.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Integration test: switch active user mid-flight and assert SiweApi.init fails rather than reporting success on the new user.
