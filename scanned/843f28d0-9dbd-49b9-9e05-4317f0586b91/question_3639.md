# Q3639: link succeeds against the wrong active user in createSiwsMessage.ts

## Question
In multi-user mode, can an attacker switch the active user between the request and the refresh inside createSiwsMessage({address so a credential is linked to one account but reported on another?

## Target
- File/function: [src/solana/createSiwsMessage.ts](src/solana/createSiwsMessage.ts) - createSiwsMessage({address, nonce, domain, uri})
- Entrypoint: privy.auth.siws flow message construction
- Attacker controls: domain, uri, address, nonce; hardcoded 'Chain ID: mainnet' and Issued At
- Exploit idea: Call the link method and switch active user while the request is in flight.
- Invariant to test: A link operation must apply to and report on a single, unchanged user id.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Integration test: switch active user mid-flight and assert createSiwsMessage({address fails rather than reporting success on the new user.
