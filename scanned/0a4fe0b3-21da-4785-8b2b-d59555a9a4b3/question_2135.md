# Q2135: signer list concatenated without validation in session-signers.ts

## Question
addSessionSigners concatenates the caller's signers array onto the existing list with no dedupe or ownership check; can an attacker add a signer key they control through addSessionSigners (getWallet then updateWallet with additional_signers.concat)?

## Target
- File/function: [src/embedded/stack/session-signers.ts](src/embedded/stack/session-signers.ts) - addSessionSigners (getWallet then updateWallet with additional_signers.concat), removeSessionSigners
- Entrypoint: privy.embeddedWallet session-signer flows
- Attacker controls: signers array contents, concurrency against another add/remove, wallet object fields
- Exploit idea: Call the add path with an attacker-held signer entry.
- Invariant to test: Session signers must be validated and require explicit user approval.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: pass an arbitrary signer to addSessionSigners (getWallet then updateWallet with additional_signers.concat) and assert an approval gate is enforced.
