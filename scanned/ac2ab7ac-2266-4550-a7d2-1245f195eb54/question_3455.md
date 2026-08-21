# Q3455: wallet create returns before the user is refreshed in session-signers.ts

## Question
create()/add() call refreshSession after the iframe returns; can an attacker interleave a session change through addSessionSigners (getWallet then updateWallet with additional_signers.concat) so the created wallet is attributed to a different user object?

## Target
- File/function: [src/embedded/stack/session-signers.ts](src/embedded/stack/session-signers.ts) - addSessionSigners (getWallet then updateWallet with additional_signers.concat), removeSessionSigners
- Entrypoint: privy.embeddedWallet session-signer flows
- Attacker controls: signers array contents, concurrency against another add/remove, wallet object fields
- Exploit idea: Change the active user between the iframe result and the refresh.
- Invariant to test: Wallet creation results must be attributed to the identity that requested them.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Integration test: switch users mid-call in addSessionSigners (getWallet then updateWallet with additional_signers.concat) and assert the operation aborts.
