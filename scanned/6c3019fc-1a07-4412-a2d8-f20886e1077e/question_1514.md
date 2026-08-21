# Q1514: on-ramp url built from server values in generate.ts

## Question
getCoinbaseOnRampUrl embeds sessionToken, partnerUserId and appId from the init response into pay.coinbase.com query parameters; can an attacker influence the init response so generateDepositAddress: body {source_chain produces a URL that funds a different partner user?

## Target
- File/function: [src/action/depositAddress/generate.ts](src/action/depositAddress/generate.ts) - generateDepositAddress: body {source_chain, source_currency, destination_chain, destination_currency, destination_address, refund_address, slippage_bps}
- Entrypoint: privy.depositAddress.generate({...})
- Attacker controls: every quote field, especially destination_address, refund_address and slippageBps
- Exploit idea: Return an init response with a foreign partner_user_id and inspect the URL.
- Invariant to test: On-ramp URL parameters must be bound to the authenticated user's session.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: return a foreign partner id and assert generateDepositAddress: body {source_chain refuses to build the URL.
