package main

import (
	"bytes"
	"testing"
)

func TestReadRequestBelowExactAndAboveBound(t *testing.T) {
	for _, test := range []struct {
		name     string
		size     int
		tooLarge bool
	}{
		{name: "below", size: maxProcessRequestBytes - 1},
		{name: "exact", size: maxProcessRequestBytes},
		{name: "above", size: maxProcessRequestBytes + 1, tooLarge: true},
	} {
		t.Run(test.name, func(t *testing.T) {
			got, tooLarge, err := readRequest(bytes.NewReader(bytes.Repeat([]byte{'x'}, test.size)))
			if err != nil {
				t.Fatalf("readRequest: %v", err)
			}
			if tooLarge != test.tooLarge {
				t.Fatalf("tooLarge = %v, want %v", tooLarge, test.tooLarge)
			}
			if !tooLarge && len(got) != test.size {
				t.Fatalf("length = %d, want %d", len(got), test.size)
			}
		})
	}
}
