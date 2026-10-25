#!/usr/bin/env python3
"""Extract bounded, human-checkable SIMD disassembly evidence from kernel object files."""
from __future__ import annotations
import argparse
import re
import subprocess
from collections import Counter
from pathlib import Path

FUNCTION = re.compile(r'^\s*[0-9a-f]+\s+<([^>]+)>:')
INSTRUCTION = re.compile(r'^\s*[0-9a-f]+:\s*([a-z][a-z0-9_.]*)\s*(.*)$', re.I)
VECTOR = re.compile(r'%?(?:xmm|ymm|zmm)\d+', re.I)
PACKED_ARITHMETIC = re.compile(r'^(?:v?(?:add|sub|mul|div|max|min|sqrt|fmadd)[a-z0-9]*?(?:ps|pd|ps[0-9]*|pd[0-9]*))$', re.I)
OPERATION = re.compile(r'^(?:v?(?:mul|add|sub|mov|fmadd|mask|blend|broadcast|insert|extract|perm|gather|shuf|xor|pxor|unpck|pack|reduce))', re.I)

def inspect_object(path: Path) -> list[str]:
    command = ['objdump', '-d', '--no-show-raw-insn', str(path)]
    output = subprocess.run(command, check=True, text=True, capture_output=True).stdout
    sections: dict[str, list[tuple[str, str]]] = {}
    name = None
    for line in output.splitlines():
        match = FUNCTION.match(line)
        if match:
            name = match.group(1)
            sections[name] = []
            continue
        opcode = INSTRUCTION.match(line)
        if name and opcode:
            sections[name].append((opcode.group(1), opcode.group(2).strip()))
    lines = [f'OBJECT {path}']
    for function, insns in sections.items():
        if not function.startswith('kernel_'):
            continue
        vector_insns = [(op, operands) for op, operands in insns if VECTOR.search(operands) and OPERATION.match(op)]
        widths = Counter('zmm' if 'zmm' in args else 'ymm' if 'ymm' in args else 'xmm'
                         for _, args in vector_insns)
        packed = [(op, operands) for op, operands in vector_insns if PACKED_ARITHMETIC.match(op)]
        lines.append(f'  {function}: packed_arithmetic_insns={len(packed)}, register_uses={len(vector_insns)}, register_widths={dict(widths)}')
        for op, operands in (packed[:3] or vector_insns[:3]):
            lines.append(f'    evidence: {op} {operands}')
        if not packed:
            lines.append('    NO PACKED ARITHMETIC OBSERVED: xmm scalar arithmetic (e.g., mulss/mulsd) is NOT SIMD vectorization')
    return lines

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build-dir', type=Path, default=Path('build'))
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    report = [
        'Targeted disassembly evidence; object-level counts include contiguous and strided branches.',
        'xmm=128-bit, ymm=256-bit, zmm=512-bit register names. Appearance does not prove a particular loop ran.',
        'Check compiler reports, disassembly control flow, CPU feature flags and benchmark configuration.',
        'FMA is intentionally disabled via -ffp-contract=off; do not claim fused operations.',
        '',
    ]
    for variant in ('scalar', 'auto', 'avx2', 'avx512'):
        binary = args.build_dir / variant / 'kernels.o'
        if binary.exists():
            report.extend(inspect_object(binary))
            report.append('')
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text('\n'.join(report) + '\n', encoding='utf-8')
    print(f'Saved disassembly summary to {args.out}')

if __name__ == '__main__':
    main()
