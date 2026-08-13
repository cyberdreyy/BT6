# Q2381: cpi_transfer_withdraw_intermediary_ata_to_destination: withdraw accepts attacker-shaped optional accounts at closeout [candidate-intermediary-accounts-from-another] [recipient-binding]

## Question
Can an unprivileged attacker use `juplend_withdraw` with candidate intermediary accounts from another bank or integration family so `cpi_transfer_withdraw_intermediary_ata_to_destination` accepts attacker-shaped optional accounts during closeout, violating `intermediary ATA closeout after Juplend withdraw must be single-use, canonical, and non-redirectable` and causing `Critical: direct theft of redeemed assets`? Focus specifically on destination binding across intermediary ATAs, vault authorities, and final transfer-out.

## Target
- File/function: `programs/marginfi/src/instructions/juplend/withdraw.rs` / `cpi_transfer_withdraw_intermediary_ata_to_destination`
- Entrypoint: `juplend_withdraw`
- Attacker controls: candidate intermediary accounts from another bank or integration family
- Exploit idea: Probe optional reward, mint, reserve, or destination accounts used only during withdraw and therefore easy to under-validate. Focus specifically on destination binding across intermediary ATAs, vault authorities, and final transfer-out.
- Invariant to test: intermediary ATA closeout after Juplend withdraw must be single-use, canonical, and non-redirectable
- Expected Immunefi impact: Critical: direct theft of redeemed assets
- Fast validation: Supply valid-looking optional accounts from another context and assert withdraw never succeeds against them. Swap every plausible destination and assert no accepted path releases value to an unvalidated recipient.
