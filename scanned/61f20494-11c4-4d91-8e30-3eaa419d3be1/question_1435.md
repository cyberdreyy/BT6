# Q1435: state code shared across simultaneous flows in pkce.ts

## Question
privy:state_code and privy:code_verifier are single global keys; can an attacker cause generateState to consume a verifier written by a different, concurrently started flow?

## Target
- File/function: [src/pkce.ts](src/pkce.ts) - generateState, generateCodeVerifier, generateCodeChallenge (S256), privy:state_code / privy:code_verifier storage keys
- Entrypoint: privy.auth.oauth.generateURL() -> storage puts
- Attacker controls: interleaving of flows that share the two global storage keys, method downgrade to plain
- Exploit idea: Start a login OAuth flow and a recovery OAuth flow, then complete the first with the second's stored verifier.
- Invariant to test: Each authorization flow must consume only the PKCE material it generated.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: start two flows against one Storage and assert the second generateURL does not overwrite the first flow's verifier before it completes.
