#!/usr/bin/env python3
"""Read GGUF metadata without loading the model into RAM.

Usage:
    python gguf_info.py <model.gguf> [key ...]

With no keys it prints the interesting subset (architecture, sizes, context,
vocab, and whether the file carries its own chat template).
"""
from __future__ import annotations

import json
import struct
import sys

# GGUF value type ids
(U8, I8, U16, I16, U32, I32, F32, BOOL, STRING, ARRAY, U64, I64, F64) = range(13)

_SCALARS = {
    U8: ("<B", 1), I8: ("<b", 1), U16: ("<H", 2), I16: ("<h", 2),
    U32: ("<I", 4), I32: ("<i", 4), F32: ("<f", 4), BOOL: ("<?", 1),
    U64: ("<Q", 8), I64: ("<q", 8), F64: ("<d", 8),
}


class Reader:
    def __init__(self, fh):
        self.fh = fh

    def read(self, n):
        buf = self.fh.read(n)
        if len(buf) != n:
            raise EOFError(f"short read: wanted {n}, got {len(buf)}")
        return buf

    def u32(self):
        return struct.unpack("<I", self.read(4))[0]

    def u64(self):
        return struct.unpack("<Q", self.read(8))[0]

    def string(self):
        n = self.u64()
        return self.read(n).decode("utf-8", errors="replace")

    def value(self, vtype):
        if vtype in _SCALARS:
            fmt, size = _SCALARS[vtype]
            return struct.unpack(fmt, self.read(size))[0]
        if vtype == STRING:
            return self.string()
        if vtype == ARRAY:
            elem = self.u32()
            count = self.u64()
            # Arrays can be huge; keep them bounded but lossless for small ones.
            out = []
            for _ in range(count):
                v = self.value(elem)
                if len(out) < 4096:
                    out.append(v)
            return out
        raise ValueError(f"unknown value type {vtype}")


def read_metadata(path):
    with open(path, "rb") as fh:
        r = Reader(fh)
        magic = r.read(4)
        if magic != b"GGUF":
            raise ValueError(f"not a GGUF file: {magic!r}")
        version = r.u32()
        tensor_count = r.u64()
        kv_count = r.u64()
        meta = {}
        for _ in range(kv_count):
            key = r.string()
            vtype = r.u32()
            meta[key] = r.value(vtype)
        return version, tensor_count, meta


INTERESTING = [
    "general.architecture",
    "general.name",
    "general.description",
    "general.size_label",
    "general.parameter_count",
    "general.file_type",
    "general.quantization_version",
    "general.basename",
    "general.finetune",
    "general.tags",
    "general.license",
    "general.source.url",
    "general.chat_template",
    "tokenizer.ggml.pre",
    "tokenizer.ggml.model",
    "tokenizer.chat_template",
    "context_length",
    "block_count",
    "embedding_length",
    "feed_forward_length",
    "attention.head_count",
    "attention.head_count_kv",
    "rope.dimension_count",
    "expert_count",
    "expert_used_count",
    "expert_feed_forward_length",
    "vocab_only_size",
    "ssm.conv_kernel",
    "ssm.inner_size",
]


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2
    # Windows consoles default to cp1252; the chat template holds fullwidth
    # markers it cannot encode.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass
    path = argv[1]
    version, tensors, meta = read_metadata(path)
    print(f"file            : {path}")
    print(f"gguf version    : {version}   tensors: {tensors}   kv pairs: {len(meta)}")

    # llama.cpp stores per-arch keys as "<arch>.<name>", so try the plain name
    # first and then the arch-prefixed spelling.
    arch = meta.get("general.architecture", "")
    wanted = argv[2:] or INTERESTING
    resolved = []
    for key in wanted:
        if key in meta:
            resolved.append(key)
        elif f"{arch}.{key}" in meta:
            resolved.append(f"{arch}.{key}")
    for key in resolved:
        if key not in meta:
            continue
        val = meta[key]
        if isinstance(val, str) and len(val) > 1200:
            print(f"{key:34}: <{len(val)} chars>")
            print(f"{'':34}  {val[:600]}")
            print(f"{'':34}  ... [truncated]")
            continue
        if isinstance(val, list):
            shown = val[:12]
            print(f"{key:34}: [{len(val)} items] {json.dumps(shown, default=str)[:400]}")
            continue
        print(f"{key:34}: {val}")

    # surface every key so nothing is silently missed
    if len(argv) > 2:
        print("\n--- all metadata keys ---")
        for k in sorted(meta):
            v = meta[k]
            if isinstance(v, str):
                v = v if len(v) <= 80 else v[:80] + f"...<{len(meta[k])} chars>"
            print(f"  {k} = {v}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
