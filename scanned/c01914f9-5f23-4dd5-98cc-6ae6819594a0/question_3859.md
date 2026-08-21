# Q3859: uppercase or checksummed address mismatch in createSiwsMessage.ts

## Question
Can an attacker exploit address case handling in createSiwsMessage({address so the address used for the nonce request differs textually from the address embedded in the signed message?

## Target
- File/function: [src/solana/createSiwsMessage.ts](src/solana/createSiwsMessage.ts) - createSiwsMessage({address, nonce, domain, uri})
- Entrypoint: privy.auth.siws flow message construction
- Attacker controls: domain, uri, address, nonce; hardcoded 'Chain ID: mainnet' and Issued At
- Exploit idea: Request the nonce with a lowercase address and sign a checksummed variant.
- Invariant to test: Address comparison in src/solana/createSiwsMessage.ts must be canonical.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: feed mixed-case address pairs to createSiwsMessage({address and assert consistent canonicalisation.
