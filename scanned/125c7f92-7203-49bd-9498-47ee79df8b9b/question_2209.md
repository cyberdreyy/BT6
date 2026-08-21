# Q2209: relying party string controlled by caller in createSiwsMessage.ts

## Question
In src/solana/createSiwsMessage.ts, is the relying party supplied by the caller and echoed into the ceremony, letting an attacker start a credential ceremony scoped to a different origin than the one they occupy?

## Target
- File/function: [src/solana/createSiwsMessage.ts](src/solana/createSiwsMessage.ts) - createSiwsMessage({address, nonce, domain, uri})
- Entrypoint: privy.auth.siws flow message construction
- Attacker controls: domain, uri, address, nonce; hardcoded 'Chain ID: mainnet' and Issued At
- Exploit idea: Call createSiwsMessage({address with a relying party that is not the current origin and observe the options returned.
- Invariant to test: The relying party used by createSiwsMessage({address must be derived from the app's configured origin.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: call createSiwsMessage({address with a foreign relying party and assert the SDK refuses.
