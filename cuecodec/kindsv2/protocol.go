package kindsv2

import (
	"context"
	"encoding/base64"
	"encoding/json"

	"github.com/mishima-computing/cuecodec/internal/strictjson"
)

type Request struct {
	Protocol    string    `json:"protocol"`
	Operation   Operation `json:"operation"`
	ContextID   string    `json:"context_id,omitempty"`
	APIVersion  string    `json:"api_version,omitempty"`
	Kind        string    `json:"kind,omitempty"`
	Variant     string    `json:"variant,omitempty"`
	Profile     string    `json:"profile,omitempty"`
	InputBase64 string    `json:"input_base64,omitempty"`
}

type Response struct {
	Protocol        string            `json:"protocol"`
	Operation       Operation         `json:"operation"`
	OK              bool              `json:"ok"`
	ContextID       string            `json:"context_id,omitempty"`
	Contract        *ContractIdentity `json:"contract,omitempty"`
	AuthoritySHA256 string            `json:"authority_sha256,omitempty"`
	Artifact        *Artifact         `json:"artifact,omitempty"`
	Failure         *Failure          `json:"failure,omitempty"`
}

// Handle executes exactly one request.  Every failure response omits Artifact.
func (r *Registry) Handle(ctx context.Context, request Request) Response {
	response := Response{
		Protocol: ProtocolV1, Operation: request.Operation,
		ContextID: request.ContextID,
	}
	if r != nil {
		response.AuthoritySHA256 = r.authoritySHA256
	}
	if request.Protocol != ProtocolV1 {
		return response.withFailure(failure(CodeFraming, request.Operation, request.ContextID, "", "", "protocol"))
	}
	requiresContext := request.Operation == OperationEmit || request.Operation == OperationParse ||
		request.Operation == OperationVet || request.Operation == OperationProject || request.Operation == OperationSchema
	if requiresContext {
		// Admission precedes identity and framing so every pending catalog row has
		// one stable, payload-free NOT_ADMITTED result regardless of caller data.
		if err := r.resolve(ctx, request.Operation, request.ContextID); err != nil {
			return response.withError(err)
		}
		contract, ok := r.contractsByContext[request.ContextID]
		if !ok {
			return response.withFailure(failure(CodeNotAdmitted, request.Operation, request.ContextID, "", "", "context-not-admitted"))
		}
		response.Contract = &contract
		if err := checkRequestedIdentity(request, contract); err != nil {
			return response.withFailure(err)
		}
	} else if request.Operation != OperationHandshake && request.Operation != OperationInspect {
		return response.withFailure(failure(CodeFraming, request.Operation, request.ContextID, "", "", "operation"))
	}

	switch request.Operation {
	case OperationHandshake:
		info, err := r.Handshake(ctx)
		if err != nil {
			return response.withError(err)
		}
		artifact, err := jsonArtifact("application/json; profile=codec-handshake-v1", info)
		if err != nil {
			return response.withFailure(failure(CodeProjection, request.Operation, request.ContextID, "", "", "handshake-json"))
		}
		return response.withArtifact(artifact)
	case OperationInspect:
		inspection, err := r.Inspect(ctx)
		if err != nil {
			return response.withError(err)
		}
		artifact, err := jsonArtifact("application/json; profile=registry-inspection-v1", inspection)
		if err != nil {
			return response.withFailure(failure(CodeProjection, request.Operation, request.ContextID, "", "", "inspection-json"))
		}
		return response.withArtifact(artifact)
	case OperationEmit, OperationParse, OperationVet, OperationProject:
		input, err := decodeInput(request)
		if err != nil {
			return response.withFailure(err)
		}
		switch request.Operation {
		case OperationEmit:
			artifact, err := r.Emit(ctx, request.ContextID, input)
			if err != nil {
				return response.withError(err)
			}
			return response.withArtifact(artifact)
		case OperationParse:
			artifact, err := r.Parse(ctx, request.ContextID, input)
			if err != nil {
				return response.withError(err)
			}
			return response.withArtifact(artifact)
		case OperationProject:
			artifact, err := r.Project(ctx, request.ContextID, request.Profile, input)
			if err != nil {
				return response.withError(err)
			}
			return response.withArtifact(artifact)
		case OperationVet:
			if err := r.Vet(ctx, request.ContextID, input); err != nil {
				return response.withError(err)
			}
			response.OK = true
			return response
		}
	case OperationSchema:
		artifact, err := r.Schema(ctx, request.ContextID, request.Profile)
		if err != nil {
			return response.withError(err)
		}
		return response.withArtifact(artifact)
	default:
		return response.withFailure(failure(CodeFraming, request.Operation, request.ContextID, "", "", "operation"))
	}
	return response.withFailure(failure(CodeFraming, request.Operation, request.ContextID, "", "", "operation"))
}

func DecodeRequest(src []byte) (Request, *Failure) {
	root, err := strictjson.Parse(src)
	if err != nil {
		if parseErr, ok := err.(*strictjson.Error); ok {
			if parseErr.Code == strictjson.DuplicateKey {
				return Request{}, failure(CodeFraming, "", "", "", parseErr.Path, "duplicate-key")
			}
			return Request{}, failure(CodeFraming, "", "", "", parseErr.Path, "request-json")
		}
		return Request{}, failure(CodeFraming, "", "", "", "", "request-json")
	}
	if root.Kind != strictjson.Object {
		return Request{}, failure(CodeFraming, "", "", "", "", "request-object")
	}
	var request Request
	if err := json.Unmarshal(src, &request); err != nil {
		return Request{}, failure(CodeFraming, "", "", "", "", "request-json")
	}
	return request, nil
}

func checkRequestedIdentity(request Request, contract ContractIdentity) *Failure {
	for _, item := range []struct{ name, got, want string }{
		{"apiVersion", request.APIVersion, contract.APIVersion},
		{"kind", request.Kind, contract.Kind},
		{"variant", request.Variant, contract.Variant},
	} {
		if item.got != "" && item.got != item.want {
			return failure(CodeIdentity, request.Operation, request.ContextID, item.name, "", "kind-mismatch")
		}
	}
	return nil
}

func decodeInput(request Request) ([]byte, *Failure) {
	if request.InputBase64 == "" {
		return nil, failure(CodeFraming, request.Operation, request.ContextID, "", "", "input-missing")
	}
	decoded, err := base64.StdEncoding.Strict().DecodeString(request.InputBase64)
	if err != nil {
		return nil, failure(CodeFraming, request.Operation, request.ContextID, "", "", "input-base64")
	}
	return decoded, nil
}

func (response Response) withArtifact(artifact Artifact) Response {
	response.OK = true
	response.Artifact = &artifact
	response.Failure = nil
	return response
}

func (response Response) withFailure(err *Failure) Response {
	response.OK = false
	response.Artifact = nil
	response.Failure = err
	return response
}

func (response Response) withError(err error) Response {
	if codecFailure, ok := err.(*Failure); ok {
		return response.withFailure(codecFailure)
	}
	return response.withFailure(failure(CodeProjection, response.Operation, response.ContextID, "", "", "internal"))
}
