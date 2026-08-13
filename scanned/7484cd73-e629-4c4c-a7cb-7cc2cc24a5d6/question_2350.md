# Q2350: cpi_transfer_withdraw_intermediary_ata_to_destination: withdraw refresh and redeem operate on different external state [candidate-intermediary-accounts-from-another] [round-trip]

## Question
Can an unprivileged attacker invoke `juplend_withdraw` with candidate intermediary accounts from another bank or integration family so `cpi_transfer_withdraw_intermediary_ata_to_destination` refreshes one external state object but redeems another, violating `intermediary ATA closeout after Juplend withdraw must be single-use, canonical, and non-redirectable` and leading to `Critical: direct theft of redeemed assets`? Focus specifically on withdraw/deposit round-trips around the integration boundary for asymmetric conversions.

## Target
- File/function: `programs/marginfi/src/instructions/juplend/withdraw.rs` / `cpi_transfer_withdraw_intermediary_ata_to_destination`
- Entrypoint: `juplend_withdraw`
- Attacker controls: candidate intermediary accounts from another bank or integration family
- Exploit idea: Where refresh precedes withdraw, ensure the refreshed reserve/obligation/position is exactly the one later burned or redeemed. Focus specifically on withdraw/deposit round-trips around the integration boundary for asymmetric conversions.
- Invariant to test: intermediary ATA closeout after Juplend withdraw must be single-use, canonical, and non-redirectable
- Expected Immunefi impact: Critical: direct theft of redeemed assets
- Fast validation: Feed mismatched external contexts and assert withdraw rejects unless refresh and redeem are bound to the same object. Run deposit-then-withdraw and withdraw-then-deposit loops and assert no positive attacker value emerges.
