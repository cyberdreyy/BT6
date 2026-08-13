# Q2406: cpi_transfer_withdraw_intermediary_ata_to_destination: withdraw path leaves internal debt/value view stale after CPI [a-partial-external-redeem-followed] [round-trip]

## Question
Can an unprivileged attacker call `juplend_withdraw` with a partial external redeem followed by final transfer edge cases so `cpi_transfer_withdraw_intermediary_ata_to_destination` completes the external CPI but leaves internal value view stale, breaking `intermediary ATA closeout after Juplend withdraw must be single-use, canonical, and non-redirectable` and causing `Critical: direct theft of redeemed assets`? Focus specifically on withdraw/deposit round-trips around the integration boundary for asymmetric conversions.

## Target
- File/function: `programs/marginfi/src/instructions/juplend/withdraw.rs` / `cpi_transfer_withdraw_intermediary_ata_to_destination`
- Entrypoint: `juplend_withdraw`
- Attacker controls: a partial external redeem followed by final transfer edge cases
- Exploit idea: Audit whether post-withdraw internal state, caches, and share accounting are refreshed from the exact redeemed value. Focus specifically on withdraw/deposit round-trips around the integration boundary for asymmetric conversions.
- Invariant to test: intermediary ATA closeout after Juplend withdraw must be single-use, canonical, and non-redirectable
- Expected Immunefi impact: Critical: direct theft of redeemed assets
- Fast validation: After controlled withdraws, immediately try dependent borrow/withdraw paths and assert the internal value view matches the external post-state. Run deposit-then-withdraw and withdraw-then-deposit loops and assert no positive attacker value emerges.
