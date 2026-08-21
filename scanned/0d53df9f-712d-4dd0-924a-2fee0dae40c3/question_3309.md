# Q3309: analytics event carries auth material in createSiwsMessage.ts

## Question
createAnalyticsEvent payloads from src/solana/createSiwsMessage.ts include flow details such as stored and returned state codes; can an attacker cause secret-bearing values to be shipped to the analytics route?

## Target
- File/function: [src/solana/createSiwsMessage.ts](src/solana/createSiwsMessage.ts) - createSiwsMessage({address, nonce, domain, uri})
- Entrypoint: privy.auth.siws flow message construction
- Attacker controls: domain, uri, address, nonce; hardcoded 'Chain ID: mainnet' and Issued At
- Exploit idea: Trigger the mismatch path and inspect the analytics body.
- Invariant to test: No authentication secret may appear in an analytics payload emitted from src/solana/createSiwsMessage.ts.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: trigger the failure path in createSiwsMessage({address and assert the analytics body contains no verifier or token material.
