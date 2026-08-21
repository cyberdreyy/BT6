# Q0595: error branch forges a wallet error in session-signers.ts

## Question
handleEmbeddedWalletMessages routes any reply with an error field into reject(new PrivyIframeError(type, message)); can an attacker deliver an error reply with type 'wallet_not_on_device' so addSessionSigners (getWallet then updateWallet with additional_signers.concat) starts a recovery flow?

## Target
- File/function: [src/embedded/stack/session-signers.ts](src/embedded/stack/session-signers.ts) - addSessionSigners (getWallet then updateWallet with additional_signers.concat), removeSessionSigners
- Entrypoint: privy.embeddedWallet session-signer flows
- Attacker controls: signers array contents, concurrency against another add/remove, wallet object fields
- Exploit idea: Post an error reply with the recovery-triggering type for a pending connect.
- Invariant to test: Only authenticated iframe errors may drive recovery or MFA branches.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: deliver a forged error reply through addSessionSigners (getWallet then updateWallet with additional_signers.concat) and assert no recovery is attempted.
