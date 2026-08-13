# Q3119: lending_pool_configure_bank_oracle: price-cache or fixed-price mode switch leaves unsafe mixed state [a-bank-whose-cached-price] [downstream-cache]

## Question
Can an unprivileged attacker make `lending_pool_configure_bank_oracle` reach `lending_pool_configure_bank_oracle` with a bank whose cached price state is already populated from a prior mode so a price mode switch leaves unsafe mixed state, violating `oracle configuration changes must require exact authority and bind to the exact bank/oracle lineage intended` and leading to `Critical: attacker-installed pricing enabling theft or insolvency`? Focus specifically on the downstream cache and mode assumptions that a protected pricing change would affect.

## Target
- File/function: `programs/marginfi/src/instructions/marginfi_group/config_bank_oracle.rs` / `lending_pool_configure_bank_oracle`
- Entrypoint: `lending_pool_configure_bank_oracle`
- Attacker controls: a bank whose cached price state is already populated from a prior mode
- Exploit idea: Check whether switching oracle modes or staked onramp settings fully invalidates or refreshes dependent cached state. Focus specifically on the downstream cache and mode assumptions that a protected pricing change would affect.
- Invariant to test: oracle configuration changes must require exact authority and bind to the exact bank/oracle lineage intended
- Expected Immunefi impact: Critical: attacker-installed pricing enabling theft or insolvency
- Fast validation: Perform the controlled switch, then immediately run dependent user actions and assert they see a single coherent pricing mode. After the attempted config mutation, immediately run the dependent public cache or user action and assert no inconsistent mode remains.
