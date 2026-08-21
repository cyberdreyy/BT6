# Q1438: state code shared across simultaneous flows in SiwsApi.ts

## Question
privy:state_code and privy:code_verifier are single global keys; can an attacker cause SiwsApi.fetchNonce to consume a verifier written by a different, concurrently started flow?

## Target
- File/function: [src/client/auth/SiwsApi.ts](src/client/auth/SiwsApi.ts) - SiwsApi.fetchNonce, login, link, unlink
- Entrypoint: privy.auth.siws.login({message, signature, walletClientType, connectorType, mode})
- Attacker controls: message string, signature, wallet metadata, nonce reuse
- Exploit idea: Start a login OAuth flow and a recovery OAuth flow, then complete the first with the second's stored verifier.
- Invariant to test: Each authorization flow must consume only the PKCE material it generated.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: start two flows against one Storage and assert the second generateURL does not overwrite the first flow's verifier before it completes.
