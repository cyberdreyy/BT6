# Q2326: cpi_transfer_withdraw_intermediary_ata_to_destination: withdraw targets the wrong recipient or vault authority [a-partial-external-redeem-followed] [round-trip]

## Question
Can an unprivileged attacker use `juplend_withdraw` with a partial external redeem followed by final transfer edge cases so `cpi_transfer_withdraw_intermediary_ata_to_destination` sends withdrawn assets to the wrong recipient or through the wrong vault authority, breaking `intermediary ATA closeout after Juplend withdraw must be single-use, canonical, and non-redirectable` and causing `Critical: direct theft of redeemed assets`? Focus specifically on withdraw/deposit round-trips around the integration boundary for asymmetric conversions.

## Target
- File/function: `programs/marginfi/src/instructions/juplend/withdraw.rs` / `cpi_transfer_withdraw_intermediary_ata_to_destination`
- Entrypoint: `juplend_withdraw`
- Attacker controls: a partial external redeem followed by final transfer edge cases
- Exploit idea: Probe destination binding and PDA authority checks across the final transfer-out phase. Focus specifically on withdraw/deposit round-trips around the integration boundary for asymmetric conversions.
- Invariant to test: intermediary ATA closeout after Juplend withdraw must be single-use, canonical, and non-redirectable
- Expected Immunefi impact: Critical: direct theft of redeemed assets
- Fast validation: Swap destinations and authorities in the controlled setup and assert no accepted path transfers value to an unvalidated account. Run deposit-then-withdraw and withdraw-then-deposit loops and assert no positive attacker value emerges.
