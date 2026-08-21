# Q2471: root wallet chosen by index order in generateWalletIdempotencyKey.ts

## Question
getRootWallet returns the first ethereum wallet, else the first solana wallet; can an attacker influence linked-account ordering so generateWalletIdempotencyKey: SHA-256 of `${userId}-auto-${eth|sol}` hex delegates under a root wallet the user did not intend?

## Target
- File/function: [src/utils/generateWalletIdempotencyKey.ts](src/utils/generateWalletIdempotencyKey.ts) - generateWalletIdempotencyKey: SHA-256 of `${userId}-auto-${eth|sol}` hex
- Entrypoint: wallet creation on login and privy.embeddedWallet.create
- Attacker controls: userId and chainType inputs; key is fully derivable from a public user id
- Exploit idea: Construct a user with several embedded wallets and observe the root chosen.
- Invariant to test: Root-wallet selection must be explicit, not positional.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: build a user with multiple wallets and assert generateWalletIdempotencyKey: SHA-256 of `${userId}-auto-${eth|sol}` hex requires an explicit root selection.
