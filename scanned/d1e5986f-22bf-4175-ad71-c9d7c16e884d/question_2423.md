# Q2423: cpi_transfer_withdraw_intermediary_ata_to_destination: withdraw round-trip with deposit leaks value across the integration boundary [duplicate-metas-altering-intermediary-or] [recipient-binding]

## Question
Can an unprivileged attacker cycle `juplend_withdraw` with duplicate metas altering intermediary or destination interpretation so `cpi_transfer_withdraw_intermediary_ata_to_destination` leaks value when combined with the matching deposit path, violating `intermediary ATA closeout after Juplend withdraw must be single-use, canonical, and non-redirectable` and causing `Critical: direct theft of redeemed assets`? Focus specifically on destination binding across intermediary ATAs, vault authorities, and final transfer-out.

## Target
- File/function: `programs/marginfi/src/instructions/juplend/withdraw.rs` / `cpi_transfer_withdraw_intermediary_ata_to_destination`
- Entrypoint: `juplend_withdraw`
- Attacker controls: duplicate metas altering intermediary or destination interpretation
- Exploit idea: Look for asymmetric conversions or fees where deposit and withdraw are not true economic inverses around edge amounts. Focus specifically on destination binding across intermediary ATAs, vault authorities, and final transfer-out.
- Invariant to test: intermediary ATA closeout after Juplend withdraw must be single-use, canonical, and non-redirectable
- Expected Immunefi impact: Critical: direct theft of redeemed assets
- Fast validation: Run deposit-then-withdraw and withdraw-then-deposit cycles near boundaries and assert no cycle creates positive attacker value. Swap every plausible destination and assert no accepted path releases value to an unvalidated recipient.
