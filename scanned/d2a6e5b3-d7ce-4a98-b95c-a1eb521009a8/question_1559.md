# Q1559: cpi_transfer_obligation_owner_to_destination: withdraw targets the wrong recipient or vault authority [duplicate-metas-that-alter-which] [recipient-binding]

## Question
Can an unprivileged attacker use `kamino_withdraw` with duplicate metas that alter which destination is interpreted as canonical so `cpi_transfer_obligation_owner_to_destination` sends withdrawn assets to the wrong recipient or through the wrong vault authority, breaking `final transfer-out after Kamino redemption must be bound to the canonical recipient and cannot replay or redirect value` and causing `Critical: direct theft of withdrawn assets`? Focus specifically on destination binding across intermediary ATAs, vault authorities, and final transfer-out.

## Target
- File/function: `programs/marginfi/src/instructions/kamino/withdraw.rs` / `cpi_transfer_obligation_owner_to_destination`
- Entrypoint: `kamino_withdraw`
- Attacker controls: duplicate metas that alter which destination is interpreted as canonical
- Exploit idea: Probe destination binding and PDA authority checks across the final transfer-out phase. Focus specifically on destination binding across intermediary ATAs, vault authorities, and final transfer-out.
- Invariant to test: final transfer-out after Kamino redemption must be bound to the canonical recipient and cannot replay or redirect value
- Expected Immunefi impact: Critical: direct theft of withdrawn assets
- Fast validation: Swap destinations and authorities in the controlled setup and assert no accepted path transfers value to an unvalidated account. Swap every plausible destination and assert no accepted path releases value to an unvalidated recipient.
