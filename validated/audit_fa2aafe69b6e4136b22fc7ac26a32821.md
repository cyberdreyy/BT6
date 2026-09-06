[1](#0-0) [2](#0-1)

### Citations

**File:** stacks-signer/src/monitoring/mod.rs (L75-81)
```rust
    /// Increment the block validation responses counter
    pub fn increment_block_validation_responses(accepted: bool) {
        let label_value = if accepted { "accepted" } else { "rejected" };
        BLOCK_VALIDATION_RESPONSES
            .with_label_values(&[label_value])
            .inc();
    }
```

**File:** stacks-signer/src/monitoring/mod.rs (L205-206)
```rust
    /// Increment the block validation responses counter
    pub fn increment_block_validation_responses(_accepted: bool) {}
```
