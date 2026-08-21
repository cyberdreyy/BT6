# Q0449: mode parameter escalates link into login in createSiwsMessage.ts

## Question
Can an unprivileged attacker pass a mode value to createSiwsMessage({address that turns an account-linking action into a login-or-sign-up, so the credential they control becomes a new authenticated session rather than a link on the existing account?

## Target
- File/function: [src/solana/createSiwsMessage.ts](src/solana/createSiwsMessage.ts) - createSiwsMessage({address, nonce, domain, uri})
- Entrypoint: privy.auth.siws flow message construction
- Attacker controls: domain, uri, address, nonce; hardcoded 'Chain ID: mainnet' and Issued At
- Exploit idea: Call privy.auth.siws flow message construction with the mode field flipped and inspect which route and which session-update path executes.
- Invariant to test: The mode argument must never let a caller convert a link request into a session-issuing login inside src/solana/createSiwsMessage.ts.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: call createSiwsMessage({address with each accepted mode and assert updateWithTokensResponse is only reached for genuine login modes.
