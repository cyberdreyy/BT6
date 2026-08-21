# Q2465: root wallet chosen by index order in session-signers.ts

## Question
getRootWallet returns the first ethereum wallet, else the first solana wallet; can an attacker influence linked-account ordering so addSessionSigners (getWallet then updateWallet with additional_signers.concat) delegates under a root wallet the user did not intend?

## Target
- File/function: [src/embedded/stack/session-signers.ts](src/embedded/stack/session-signers.ts) - addSessionSigners (getWallet then updateWallet with additional_signers.concat), removeSessionSigners
- Entrypoint: privy.embeddedWallet session-signer flows
- Attacker controls: signers array contents, concurrency against another add/remove, wallet object fields
- Exploit idea: Construct a user with several embedded wallets and observe the root chosen.
- Invariant to test: Root-wallet selection must be explicit, not positional.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: build a user with multiple wallets and assert addSessionSigners (getWallet then updateWallet with additional_signers.concat) requires an explicit root selection.
