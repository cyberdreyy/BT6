# Q1329: 30 day refresh cookie on a shared browser in createSiwsMessage.ts

## Question
Session.storeRefreshTokenForUser sets a 30-day refresh cookie; after createSiwsMessage({address, can a later unprivileged user of the same browser profile resume the previous account because logout only clears the active user's keys?

## Target
- File/function: [src/solana/createSiwsMessage.ts](src/solana/createSiwsMessage.ts) - createSiwsMessage({address, nonce, domain, uri})
- Entrypoint: privy.auth.siws flow message construction
- Attacker controls: domain, uri, address, nonce; hardcoded 'Chain ID: mainnet' and Issued At
- Exploit idea: Log in, close the tab without logout, then in a new SDK instance call refreshSession and observe a restored session.
- Invariant to test: A session must not be resumable after the SDK's own clear paths have run for that user.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Integration test: run createSiwsMessage({address, call destroyLocalState, construct a fresh Privy client and assert refreshSession throws MISSING_OR_INVALID_TOKEN.
