### Title
Multisig Request Can Execute Below Confirmation Threshold After a Confirming Member Is Deleted - (File: `multisig2/src/lib.rs`)

### Summary
`MultiSigContract::confirm` only checks `confirmations.len() + 1 >= num_confirmations`, without verifying that every account whose approval is counted is still a live member. `delete_member` purges stale requests and `num_requests_pk` only for the member's *own created requests*, but never scrubs that member's confirmation entries recorded on requests created by *other* members. A request can therefore be executed with fewer live-member approvals than `num_confirmations` requires.

### Finding Description
`confirm()` increments/checks the `confirmations` `HashSet<String>` for a request and executes as soon as the size reaches `num_confirmations`: [1](#0-0) 

The only cleanup of stale confirmation entries happens inside `delete_member`, which remov

### Citations

**File:** multisig2/src/lib.rs (L292-315)
```rust
    /// Confirm given request with given signing key.
    /// If with this, there has been enough confirmation, a promise with request will be scheduled.
    pub fn confirm(&mut self, request_id: RequestId) -> PromiseOrValue<bool> {
        self.assert_valid_request(request_id);
        let member = self
            .current_member()
            .unwrap_or_else(|| env::panic_str("Must be validated above"));
        let mut confirmations = self.confirmations.get(&request_id).unwrap();
        assert(
            !confirmations.contains(&member.to_string()),
            "Already confirmed this request with this key",
        );
        if confirmations.len() as u32 + 1 >= self.num_confirmations {
            let request = self.remove_request(request_id);
            /********************************
            NOTE: If the tx execution fails for any reason, the request and confirmations are removed already, so the client has to start all over
            ********************************/
            self.execute_request(request)
        } else {
            confirmations.insert(member.to_string());
            self.confirmations.insert(&request_id, &confirmations);
            PromiseOrValue::Value(true)
        }
    }
```
