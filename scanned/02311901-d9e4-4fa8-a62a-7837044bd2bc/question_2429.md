# Q2429: challenge not bound to the stored options in createSiwsMessage.ts

## Question
Does createSiwsMessage({address accept a challenge argument supplied by the caller rather than the one returned by the matching options call, enabling replay of a previously captured assertion?

## Target
- File/function: [src/solana/createSiwsMessage.ts](src/solana/createSiwsMessage.ts) - createSiwsMessage({address, nonce, domain, uri})
- Entrypoint: privy.auth.siws flow message construction
- Attacker controls: domain, uri, address, nonce; hardcoded 'Chain ID: mainnet' and Issued At
- Exploit idea: Call the options method, discard the challenge, and log in with an older challenge plus its captured assertion.
- Invariant to test: The challenge submitted must be the one issued for this ceremony.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: pass a stale challenge to createSiwsMessage({address and assert it is rejected.
