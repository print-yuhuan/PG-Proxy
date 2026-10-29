package waa

import (
	"crypto/sha256"
	"encoding/base64"
)

// Challenge 保存 Waa/Create 返回的客户端解释器输入
type Challenge struct {
	InterpreterURL             string
	InterpreterHash            string
	Program                    string
	GlobalName                 string
	ClientExperimentsStateBlob string
}

// InterpreterHash 返回解释器源码的 SHA-256 base64url 摘要
func InterpreterHash(source []byte) string {
	digest := sha256.Sum256(source)
	return base64.RawURLEncoding.EncodeToString(digest[:])
}
