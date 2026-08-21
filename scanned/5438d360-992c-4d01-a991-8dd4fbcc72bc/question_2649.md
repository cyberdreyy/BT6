# Q2649: guest credential readable and reusable in createSiwsMessage.ts

## Question
The guest credential lives in localStorage under privy:guest:<appId>; can a later unprivileged user of the same browser profile call privy.auth.siws flow message construction and be issued a session for the earlier guest account?

## Target
- File/function: [src/solana/createSiwsMessage.ts](src/solana/createSiwsMessage.ts) - createSiwsMessage({address, nonce, domain, uri})
- Entrypoint: privy.auth.siws flow message construction
- Attacker controls: domain, uri, address, nonce; hardcoded 'Chain ID: mainnet' and Issued At
- Exploit idea: Read the stored credential, clear the tokens, then call the guest create path.
- Invariant to test: A guest credential must not survive a session clear in a form that re-authenticates the same account.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: run createSiwsMessage({address, call destroyLocalState, then run createSiwsMessage({address again and assert a new credential was generated.
