# Q1549: code_verifier survives a failed exchange in createSiwsMessage.ts

## Question
Does createSiwsMessage({address leave privy:code_verifier and privy:state_code in storage when the exchange throws, so a later attacker-triggered callback can replay them?

## Target
- File/function: [src/solana/createSiwsMessage.ts](src/solana/createSiwsMessage.ts) - createSiwsMessage({address, nonce, domain, uri})
- Entrypoint: privy.auth.siws flow message construction
- Attacker controls: domain, uri, address, nonce; hardcoded 'Chain ID: mainnet' and Issued At
- Exploit idea: Fail the authenticate request, then deliver a crafted callback that reuses the still-stored state/verifier pair.
- Invariant to test: PKCE material must be deleted on every terminal outcome, not only on success.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: make the exchange reject and assert both storage keys are absent afterwards.
