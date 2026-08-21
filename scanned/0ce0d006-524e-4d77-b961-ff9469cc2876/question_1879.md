# Q1879: domain and uri are caller-controlled in createSiwsMessage.ts

## Question
createSiwsMessage({address builds the signing statement from a caller-supplied domain and uri; can an attacker present a message whose domain names a different application so a signature harvested elsewhere authenticates here?

## Target
- File/function: [src/solana/createSiwsMessage.ts](src/solana/createSiwsMessage.ts) - createSiwsMessage({address, nonce, domain, uri})
- Entrypoint: privy.auth.siws flow message construction
- Attacker controls: domain, uri, address, nonce; hardcoded 'Chain ID: mainnet' and Issued At
- Exploit idea: Build a message with the victim app's domain, obtain a signature in another context, and submit it.
- Invariant to test: The signed statement must be bound to the origin actually performing the authentication.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: assert createSiwsMessage({address rejects a domain that does not match the configured app origin.
