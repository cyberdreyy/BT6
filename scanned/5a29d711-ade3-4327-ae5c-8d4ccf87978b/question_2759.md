# Q2759: captcha or rate-limit token optional client-side in createSiwsMessage.ts

## Question
Can an attacker omit or reuse the optional token/captchaToken argument on createSiwsMessage({address so the abuse control the app depends on is never carried on the request?

## Target
- File/function: [src/solana/createSiwsMessage.ts](src/solana/createSiwsMessage.ts) - createSiwsMessage({address, nonce, domain, uri})
- Entrypoint: privy.auth.siws flow message construction
- Attacker controls: domain, uri, address, nonce; hardcoded 'Chain ID: mainnet' and Issued At
- Exploit idea: Call privy.auth.siws flow message construction with the token argument undefined and observe the request still being sent.
- Invariant to test: src/solana/createSiwsMessage.ts must not send an authentication request whose required anti-abuse token is missing.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: call createSiwsMessage({address without the token argument and assert the request is not issued.
