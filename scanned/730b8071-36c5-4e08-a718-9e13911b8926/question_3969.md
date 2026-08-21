# Q3969: no expiry in the signed statement in createSiwsMessage.ts

## Question
The statement built in src/solana/createSiwsMessage.ts carries Issued At but no expiration; can an attacker replay a signature captured months earlier through createSiwsMessage({address?

## Target
- File/function: [src/solana/createSiwsMessage.ts](src/solana/createSiwsMessage.ts) - createSiwsMessage({address, nonce, domain, uri})
- Entrypoint: privy.auth.siws flow message construction
- Attacker controls: domain, uri, address, nonce; hardcoded 'Chain ID: mainnet' and Issued At
- Exploit idea: Sign once, store the message and signature, replay after a long delay.
- Invariant to test: Authentication statements must carry an expiry the client enforces.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: assert createSiwsMessage({address rejects a message whose Issued At is older than a short window.
