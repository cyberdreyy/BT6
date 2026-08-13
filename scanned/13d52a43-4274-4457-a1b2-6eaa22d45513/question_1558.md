# Q1558: cpi_transfer_obligation_owner_to_destination: withdraw targets the wrong recipient or vault authority [a-final-transfer-after-partial] [round-trip]

## Question
Can an unprivileged attacker use `kamino_withdraw` with a final transfer after partial CPI success/failure conditions so `cpi_transfer_obligation_owner_to_destination` sends withdrawn assets to the wrong recipient or through the wrong vault authority, breaking `final transfer-out after Kamino redemption must be bound to the canonical recipient and cannot replay or redirect value` and causing `Critical: direct theft of withdrawn assets`? Focus specifically on withdraw/deposit round-trips around the integration boundary for asymmetric conversions.

## Target
- File/function: `programs/marginfi/src/instructions/kamino/withdraw.rs` / `cpi_transfer_obligation_owner_to_destination`
- Entrypoint: `kamino_withdraw`
- Attacker controls: a final transfer after partial CPI success/failure conditions
- Exploit idea: Probe destination binding and PDA authority checks across the final transfer-out phase. Focus specifically on withdraw/deposit round-trips around the integration boundary for asymmetric conversions.
- Invariant to test: final transfer-out after Kamino redemption must be bound to the canonical recipient and cannot replay or redirect value
- Expected Immunefi impact: Critical: direct theft of withdrawn assets
- Fast validation: Swap destinations and authorities in the controlled setup and assert no accepted path transfers value to an unvalidated account. Run deposit-then-withdraw and withdraw-then-deposit loops and assert no positive attacker value emerges.
