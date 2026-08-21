# Q2648: guest credential readable and reusable in SiwsApi.ts

## Question
The guest credential lives in localStorage under privy:guest:<appId>; can a later unprivileged user of the same browser profile call privy.auth.siws.login({message, signature, walletClientType, connectorType, mode}) and be issued a session for the earlier guest account?

## Target
- File/function: [src/client/auth/SiwsApi.ts](src/client/auth/SiwsApi.ts) - SiwsApi.fetchNonce, login, link, unlink
- Entrypoint: privy.auth.siws.login({message, signature, walletClientType, connectorType, mode})
- Attacker controls: message string, signature, wallet metadata, nonce reuse
- Exploit idea: Read the stored credential, clear the tokens, then call the guest create path.
- Invariant to test: A guest credential must not survive a session clear in a form that re-authenticates the same account.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: run SiwsApi.fetchNonce, call destroyLocalState, then run SiwsApi.fetchNonce again and assert a new credential was generated.
