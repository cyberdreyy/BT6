# Q1443: kamino_withdraw: withdraw refresh and redeem operate on different external state [a-withdraw-amount-at-last] [recipient-binding]

## Question
Can an unprivileged attacker invoke `kamino_withdraw` with a withdraw amount at last-share and tiny redemption boundaries so `kamino_withdraw` refreshes one external state object but redeems another, violating `Kamino withdraw must release only value actually redeemed from the exact obligation/reserve pair and correct recipient` and leading to `Critical: direct theft or phantom redemption`? Focus specifically on destination binding across intermediary ATAs, vault authorities, and final transfer-out.

## Target
- File/function: `programs/marginfi/src/instructions/kamino/withdraw.rs` / `kamino_withdraw`
- Entrypoint: `kamino_withdraw`
- Attacker controls: a withdraw amount at last-share and tiny redemption boundaries
- Exploit idea: Where refresh precedes withdraw, ensure the refreshed reserve/obligation/position is exactly the one later burned or redeemed. Focus specifically on destination binding across intermediary ATAs, vault authorities, and final transfer-out.
- Invariant to test: Kamino withdraw must release only value actually redeemed from the exact obligation/reserve pair and correct recipient
- Expected Immunefi impact: Critical: direct theft or phantom redemption
- Fast validation: Feed mismatched external contexts and assert withdraw rejects unless refresh and redeem are bound to the same object. Swap every plausible destination and assert no accepted path releases value to an unvalidated recipient.
