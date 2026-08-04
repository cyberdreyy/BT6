[1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** helpers/transfer/parallel_download.go (L16-39)
```go
func normalizeParallelDownloadInputs(contentLength int64, chunkSize int64, concurrency int) (int64, int, error) {
	if chunkSize <= 0 {
		return 0, 0, fmt.Errorf("transfer: chunk size must be positive")
	}
	if chunkSize > contentLength {
		chunkSize = contentLength
	}
	if concurrency < 1 {
		concurrency = 1
	}
	return chunkSize, concurrency, nil
}

func parallelDownloadRanges(contentLength, chunkSize int64) []byteRange {
	var chunks []byteRange
	for offset := int64(0); offset < contentLength; offset += chunkSize {
		length := chunkSize
		if offset+length > contentLength {
			length = contentLength - offset
		}
		chunks = append(chunks, byteRange{offset, length})
	}
	return chunks
}
```

**File:** helpers/transfer/parallel_download.go (L52-79)
```go
func (w *parallelRangeWorker) downloadChunk(offset, length int64) {
	reader, err := w.fetchChunk(offset, length)
	if err != nil {
		w.recordFirstErr(err)
		return
	}
	defer func() { _ = reader.Close() }()

	chunkLen := int(length)
	if int64(chunkLen) != length {
		w.recordFirstErr(fmt.Errorf("chunk length overflows int: %d", length))
		return
	}
	buf := make([]byte, chunkLen)
	_, err = io.ReadFull(io.LimitReader(reader, length), buf)
	if err != nil {
		w.recordFirstErr(fmt.Errorf("chunk read at offset %d: %w", offset, err))
		return
	}
	n, err := w.dest.WriteAt(buf, offset)
	if err != nil {
		w.recordFirstErr(err)
		return
	}
	if int64(n) != length {
		w.recordFirstErr(fmt.Errorf("chunk write size mismatch at offset %d: wrote %d bytes, want %d", offset, n, length))
	}
}
```

**File:** helpers/transfer/parallel_download.go (L102-110)
```go
	for _, cnk := range chunks {
		wg.Add(1)
		sem <- struct{}{}
		go func(offset, length int64) {
			defer wg.Done()
			defer func() { <-sem }()
			worker.downloadChunk(offset, length)
		}(cnk.offset, cnk.length)
	}
```

**File:** helpers/transfer/content_range.go (L12-35)
```go
// ParseContentRangeTotal returns the full representation length N from an HTTP Content-Range field value
// (RFC 9110), for example "bytes 0-0/N" or "bytes */N". It returns ok false if the value is malformed,
// the complete length is unknown ("*"), or N <= 0.
func ParseContentRangeTotal(contentRange string) (n int64, ok bool) {
	const prefix = "bytes "
	contentRange = strings.TrimSpace(contentRange)
	if !strings.HasPrefix(contentRange, prefix) {
		return 0, false
	}
	rest := strings.TrimSpace(contentRange[len(prefix):])
	slash := strings.LastIndex(rest, "/")
	if slash < 0 {
		return 0, false
	}
	totalStr := strings.TrimSpace(rest[slash+1:])
	if totalStr == "*" {
		return 0, false
	}
	parsed, err := strconv.ParseInt(totalStr, 10, 64)
	if err != nil || parsed <= 0 {
		return 0, false
	}
	return parsed, true
}
```
