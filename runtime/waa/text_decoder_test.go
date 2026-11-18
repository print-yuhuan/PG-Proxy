package waa

import "testing"

func TestFatalTextDecoderDistinguishesLiteralReplacement(t *testing.T) {
	tests := []struct {
		name     string
		encoding string
		input    []byte
		want     string
		invalid  bool
	}{
		{"UTF-16LE literal and BOM", "utf-16le", []byte{0xff, 0xfe, 0xfd, 0xff, 0x61, 0x00}, "\ufeff\ufffda", false},
		{"UTF-16BE literal", "utf-16be", []byte{0xff, 0xfd}, "\ufffd", false},
		{"UTF-16LE literal beside lone surrogate", "utf-16le", []byte{0xfd, 0xff, 0x00, 0xd8}, "\ufffd\ufffd", true},
		{"UTF-16BE surrogate and trailing byte", "utf-16be", []byte{0xd8, 0x00, 0x61}, "\ufffd\ufffd", true},
		{"GB18030 supplementary and literal", "gb18030", []byte{0x90, 0x30, 0x81, 0x30, 0x84, 0x31, 0xa4, 0x37}, "\U00010000\ufffd", false},
		{"GB18030 literal beside malformed byte", "gb18030", []byte{0x84, 0x31, 0xa4, 0x37, 0xff}, "\ufffd\ufffd", true},
		{"GB18030 malformed bytes containing UTF-8 literal", "gb18030", []byte{0xef, 0xbf, 0xbd, 0xff}, "锟\ufffd\ufffd", true},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			got, err := decodeText(tc.encoding, tc.input, true)
			if (err != nil) != tc.invalid || (!tc.invalid && got != tc.want) {
				t.Errorf("fatal decode got %q, %v; want %q, error=%v", got, err, tc.want, tc.invalid)
			}
			got, err = decodeText(tc.encoding, tc.input, false)
			if err != nil || got != tc.want {
				t.Errorf("nonfatal decode got %q, %v; want %q", got, err, tc.want)
			}
		})
	}
}
