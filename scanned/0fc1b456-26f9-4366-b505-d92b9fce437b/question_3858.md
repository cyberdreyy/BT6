# Q3858: uppercase or checksummed address mismatch in SiwsApi.ts

## Question
Can an attacker exploit address case handling in SiwsApi.fetchNonce so the address used for the nonce request differs textually from the address embedded in the signed message?

## Target
- File/function: [src/client/auth/SiwsApi.ts](src/client/auth/SiwsApi.ts) - SiwsApi.fetchNonce, login, link, unlink
- Entrypoint: privy.auth.siws.login({message, signature, walletClientType, connectorType, mode})
- Attacker controls: message string, signature, wallet metadata, nonce reuse
- Exploit idea: Request the nonce with a lowercase address and sign a checksummed variant.
- Invariant to test: Address comparison in src/client/auth/SiwsApi.ts must be canonical.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: feed mixed-case address pairs to SiwsApi.fetchNonce and assert consistent canonicalisation.
