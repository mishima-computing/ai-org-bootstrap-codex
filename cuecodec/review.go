package cuecodec

import (
	"context"
	"sort"

	"github.com/mishima-computing/cuecodec/internal/flatreview"
)

// EncodeFlatReviewV1 emits one independently framed flat-review section for
// every record in the admitted corpus selected by kind. Sections are sorted by
// stable ID. Within each section, scalar leaves and empty containers are
// represented by byte-sorted RFC 6901 JSON Pointer rows.
//
// FlatReviewV1 is output-only: it is derived from already-admitted carrier
// values and is never retained by, or supplied to, repository/CUE loading.
func (c *Codec) EncodeFlatReviewV1(ctx context.Context, kind Kind) ([]byte, error) {
	const operation = "encode_flat_review_v1"
	if err := validContext(ctx, operation, kind, ""); err != nil {
		return nil, err
	}
	entry, err := c.lookupKind(operation, kind)
	if err != nil {
		return nil, err
	}

	c.mu.RLock()
	records := make([]Record, 0, len(entry.records))
	for _, record := range entry.records {
		records = append(records, record)
	}
	c.mu.RUnlock()
	sort.Slice(records, func(i, j int) bool {
		if records[i].stableID != records[j].stableID {
			return records[i].stableID < records[j].stableID
		}
		return records[i].sourcePath < records[j].sourcePath
	})

	inputs := make([]flatreview.Record, 0, len(records))
	for _, record := range records {
		carrier, projectionErr := canonicalCarrierJSON(record.value)
		if projectionErr != nil {
			return nil, codecError(CodeProjection, operation, kind, record.sourcePath, "", "", "flat-review-v1")
		}
		inputs = append(inputs, flatreview.Record{StableID: record.stableID, Carrier: carrier})
	}
	candidate, encodeErr := flatreview.Encode(inputs)
	if encodeErr != nil {
		return nil, codecError(CodeProjection, operation, kind, "", "", "", "flat-review-v1")
	}
	if len(candidate) > c.limits.MaxBytes {
		return nil, codecError(CodeStructuralLimit, operation, kind, "", "", "", "max-bytes")
	}
	if err := c.verifySnapshot(operation, kind, ""); err != nil {
		return nil, err
	}
	return candidate, nil
}
