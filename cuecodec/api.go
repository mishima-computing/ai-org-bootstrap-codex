package cuecodec

import (
	"context"
	"sync"

	"cuelang.org/go/cue"
	"github.com/mishima-computing/cuecodec/internal/cueload"
	"github.com/mishima-computing/cuecodec/internal/repository"
)

// Kind is the complete, versioned identity of a record schema.
type Kind struct {
	APIVersion string
	Kind       string
}

// Limits bounds both accepted and evaluated public values. Zero values select
// the codec defaults.
type Limits struct {
	MaxDepth int
	MaxBytes int
}

// Options selects the repository authority and resource limits used by a
// Codec. RepositoryRoot defaults to the caller's current directory.
type Options struct {
	RepositoryRoot string
	Limits         Limits
}

// Codec is an admitted, immutable view of one repository source snapshot.
// Its representation is deliberately private so registry entries cannot be
// inserted without passing the admission proof.
type Codec struct {
	root     string
	limits   Limits
	ctx      *cue.Context
	snapshot repository.SourceSnapshotV1
	closure  cueload.ImportClosureV1

	mu      sync.RWMutex
	kinds   []Kind
	entries map[kindKey]*kindEntry
}

// Record is an immutable evaluated whole record. A Record's zero value is not
// valid input to codec operations.
type Record struct {
	owner      *Codec
	kind       Kind
	stableID   string
	sourcePath string
	value      cue.Value
}

// Kind returns the record's versioned schema identity.
func (r Record) Kind() Kind { return r.kind }

// StableID returns the durable identity embedded in the whole record.
func (r Record) StableID() string { return r.stableID }

// SourcePath returns the authoritative repository-relative path. Records
// parsed from canonical bytes have no source path and return the empty string.
func (r Record) SourcePath() string { return r.sourcePath }

// Value returns the immutable evaluated CUE value carried by the record.
func (r Record) Value() cue.Value { return r.value }

// SupportedKinds returns a defensive, byte-sorted copy of the kinds whose
// complete admission proof succeeded during construction.
func (c *Codec) SupportedKinds() []Kind {
	if c == nil {
		return nil
	}
	// SupportedKinds has no error return. A drifted authority therefore fails
	// closed by advertising no kind until a fresh Codec is constructed.
	if err := c.verifySnapshot("supported_kinds", Kind{}, ""); err != nil {
		return nil
	}
	c.mu.RLock()
	defer c.mu.RUnlock()
	return append([]Kind(nil), c.kinds...)
}

// LoadRecord loads one admitted authoritative record by repository-relative
// path. It returns the zero Record on every failure.
func (c *Codec) LoadRecord(ctx context.Context, kind Kind, recordPath string) (Record, error) {
	if err := validContext(ctx, "load_record", kind, recordPath); err != nil {
		return Record{}, err
	}
	entry, err := c.lookupKind("load_record", kind)
	if err != nil {
		return Record{}, err
	}
	if err := validateRecordPath(recordPath, "load_record", kind); err != nil {
		return Record{}, err
	}
	record, ok := entry.records[recordPath]
	if !ok {
		return Record{}, codecError(CodeRead, "load_record", kind, recordPath, "", "", "record-unavailable")
	}
	if err := c.verifySnapshot("load_record", kind, recordPath); err != nil {
		return Record{}, err
	}
	return record, nil
}

// ParseCanonical parses one exact final-cue-text-v1 whole record. Valid but
// noncanonical CUE is rejected rather than normalized for the caller.
func (c *Codec) ParseCanonical(ctx context.Context, kind Kind, src []byte) (Record, error) {
	if err := validContext(ctx, "parse_canonical", kind, ""); err != nil {
		return Record{}, err
	}
	entry, err := c.lookupKind("parse_canonical", kind)
	if err != nil {
		return Record{}, err
	}
	if len(src) > c.limits.MaxBytes {
		return Record{}, codecError(CodeInputLimit, "parse_canonical", kind, "", "", "", "max-bytes")
	}
	value := c.ctx.CompileBytes(src, cue.Filename("canonical.cue"))
	if value.Err() != nil {
		return Record{}, codecError(CodeSyntax, "parse_canonical", kind, "", "", "", "cue-syntax")
	}
	record, err := c.validateRecord("parse_canonical", entry, kind, "", value)
	if err != nil {
		return Record{}, err
	}
	canonical, err := c.encodeCanonical("parse_canonical", record)
	if err != nil {
		return Record{}, err
	}
	if !bytesEqual(src, canonical) {
		return Record{}, codecError(CodeNonCanonical, "parse_canonical", kind, "", "", "", "final-cue-text-v1")
	}
	if err := c.verifySnapshot("parse_canonical", kind, ""); err != nil {
		return Record{}, err
	}
	return record, nil
}

// EncodeCanonical emits final-cue-text-v1 bytes for a validated admitted
// record. The result is always UTF-8, LF-only, and has one terminal newline.
func (c *Codec) EncodeCanonical(ctx context.Context, record Record) ([]byte, error) {
	if err := validContext(ctx, "encode_canonical", record.kind, record.sourcePath); err != nil {
		return nil, err
	}
	if err := c.validateRecordOwner("encode_canonical", record); err != nil {
		return nil, err
	}
	result, err := c.encodeCanonical("encode_canonical", record)
	if err != nil {
		return nil, err
	}
	if err := c.verifySnapshot("encode_canonical", record.kind, record.sourcePath); err != nil {
		return nil, err
	}
	return result, nil
}

// ExportJSON emits the precision-preserving carrier JSON for a validated
// admitted record. Object members are byte-sorted and list order is retained.
func (c *Codec) ExportJSON(ctx context.Context, record Record) ([]byte, error) {
	if err := validContext(ctx, "export_json", record.kind, record.sourcePath); err != nil {
		return nil, err
	}
	if err := c.validateRecordOwner("export_json", record); err != nil {
		return nil, err
	}
	carrier, err := canonicalCarrierJSON(record.value)
	if err != nil {
		return nil, codecError(CodeProjection, "export_json", record.kind, record.sourcePath, "", "", "carrier-json")
	}
	if len(carrier) > c.limits.MaxBytes {
		return nil, codecError(CodeStructuralLimit, "export_json", record.kind, record.sourcePath, "", "", "max-bytes")
	}
	if err := c.verifySnapshot("export_json", record.kind, record.sourcePath); err != nil {
		return nil, err
	}
	return carrier, nil
}
