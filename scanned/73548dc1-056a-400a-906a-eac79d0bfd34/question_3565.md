# Q3565: solana create takes an ethereum account argument in session-signers.ts

## Question
createSolana accepts an ethereumAccount whose provider is loaded first; can an attacker pass a foreign ethereum account through addSessionSigners (getWallet then updateWallet with additional_signers.concat) so entropy from another wallet is used for the new Solana wallet?

## Target
- File/function: [src/embedded/stack/session-signers.ts](src/embedded/stack/session-signers.ts) - addSessionSigners (getWallet then updateWallet with additional_signers.concat), removeSessionSigners
- Entrypoint: privy.embeddedWallet session-signer flows
- Attacker controls: signers array contents, concurrency against another add/remove, wallet object fields
- Exploit idea: Call createSolana with an ethereum account object that is not the user's.
- Invariant to test: Cross-chain wallet derivation must use only the authenticated user's own accounts.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: pass a foreign ethereum account to addSessionSigners (getWallet then updateWallet with additional_signers.concat) and assert rejection.
