// ai-org-cuecodec is the single-shot cuecodec-process-v1 executable.  It reads
// one strict JSON request from stdin and writes one JSON response plus LF.
package main

import (
	"bytes"
	"context"
	"encoding/json"
	"io"
	"os"

	"github.com/mishima-computing/cuecodec/kindsv2"
)

const maxProcessRequestBytes = 4*((kindsv2.DefaultMaxBytes+2)/3) + (64 << 10)

func main() {
	src, tooLarge, readErr := readRequest(os.Stdin)
	request, decodeFailure := kindsv2.DecodeRequest(src)
	if tooLarge {
		request = kindsv2.Request{}
		decodeFailure = &kindsv2.Failure{Code: kindsv2.CodeFraming, RuleID: "stdin-max-bytes"}
	} else if readErr != nil {
		decodeFailure = &kindsv2.Failure{Code: kindsv2.CodeFraming, Operation: request.Operation, RuleID: "stdin"}
	}
	var response kindsv2.Response
	if decodeFailure != nil {
		response = kindsv2.Response{
			Protocol: kindsv2.ProtocolV1, Operation: request.Operation,
			OK: false, Failure: decodeFailure,
		}
	} else {
		registry, err := kindsv2.New()
		if err != nil {
			failure, ok := err.(*kindsv2.Failure)
			if !ok {
				failure = &kindsv2.Failure{Code: kindsv2.CodeAuthority, Operation: request.Operation, RuleID: "registry"}
			}
			response = kindsv2.Response{
				Protocol: kindsv2.ProtocolV1, Operation: request.Operation,
				ContextID: request.ContextID, OK: false, Failure: failure,
			}
		} else {
			response = registry.Handle(context.Background(), request)
		}
	}
	encoder := json.NewEncoder(os.Stdout)
	encoder.SetEscapeHTML(false)
	if err := encoder.Encode(response); err != nil {
		os.Exit(2)
	}
}

func readRequest(source io.Reader) ([]byte, bool, error) {
	var bounded bytes.Buffer
	_, err := io.CopyN(&bounded, source, maxProcessRequestBytes+1)
	if err != nil && err != io.EOF {
		return bounded.Bytes(), false, err
	}
	if bounded.Len() > maxProcessRequestBytes {
		return nil, true, nil
	}
	return bounded.Bytes(), false, nil
}
